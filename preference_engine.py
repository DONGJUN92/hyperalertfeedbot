import base64
import json
import logging
import math
import os
import re
import threading
import time
import zlib
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("x_preference")

# Calibrated thresholds reflecting IDF-weighted subword vector space
DISLIKE_SUPPRESS_THRESHOLD = 0.45  # Conservative threshold to prevent false positive suppression
LIKE_BOOST_THRESHOLD = 0.35        # Relaxed threshold to catch nuanced topic matches
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Stopwords to prevent generic verbs/conjunctions/boilerplate from inflating similarity
EN_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "were", "have", "has",
    "had", "not", "but", "what", "all", "can", "will", "just", "more", "about", "into", "than",
    "its", "over", "also", "new", "some", "could", "would", "should", "their", "there", "been",
    "which", "when", "who", "how", "where", "why", "out", "our", "your", "they", "them", "his",
    "her", "she", "him", "you", "via", "http", "https", "com", "org", "www", "arxiv", "abs",
    "tech", "urgent", "policy", "regulation", "macro", "research", "geeknews", "vip",
}

KO_STOPWORDS = {
    "있다", "있는", "있으며", "있습니다", "한다", "하는", "하고", "했다", "했습니다", "된다", "되는",
    "통해", "대한", "위해", "위한", "따라", "따른", "관련", "대해", "이번", "현재", "최근", "경우",
    "수준", "전망", "전망됩니다", "상황", "상황입니다", "보인다", "밝혔다", "전했다", "나타났다",
    "가운데", "대비", "기준", "예정", "계속", "모두", "가장", "매우", "크게", "특히", "또한",
    "그리고", "하지만", "그러나", "따라서", "한편", "원문", "보기", "출처", "바로가기", "브리핑",
    "발언", "링크", "감지", "키워드", "소식", "기사", "뉴스", "결과", "분석", "평가", "적용",
    "맞춤추천", "맞춤", "추천", "확장", "토픽", "공개", "발표",
}

KO_PARTICLE_SUFFIXES = (
    "에서는", "으로는", "에게는", "로부터", "까지도", "이라는", "이라며", "이라고",
    "에서", "으로", "에게", "부터", "까지", "보다", "처럼", "만큼", "이다", "이고", "이며", "이나",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "도", "만",
)

DEFAULT_BASE_QUERIES = {"상법", "개정", "자본시장법", "금융위", "공정거래위원회", "기업결합", "공정거래법"}


class PreferenceEngine:
    """
    Ultra-Lightweight Semantic Preference & Recommendation Engine (v2).
    Uses IDF-weighted subword & semantic morpheme vector space (RAM usage < 10MB)
    with title weighting, stopword filtering, stateless callback recovery,
    compressed backup/restore, and dynamic topic expansion.
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR, model_dir: str = ""):
        self.data_dir = data_dir
        self.pref_file = os.path.join(self.data_dir, "preferences.json")
        os.makedirs(self.data_dir, exist_ok=True)

        self.lock = threading.Lock()
        self.model_ready = True  # Instantly ready with 0ms delay!

        # Content cache mapping short content_id -> metadata
        # (Needed because Telegram callback_data has strict 64-byte limit)
        self.content_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()

        # Feedback store
        self.preferences: Dict[str, Any] = self._load_preferences()

        # Optional: Restore from PREF_BACKUP_CODE env var if local preferences are empty
        env_backup = os.environ.get("PREF_BACKUP_CODE", "").strip()
        if env_backup and not self.preferences.get("likes") and not self.preferences.get("dislikes"):
            ok, msg = self.import_backup_code(env_backup)
            if ok:
                logger.info(f"♻️ Restored preferences from PREF_BACKUP_CODE env var: {msg}")

        logger.info("✅ Ultra-Lightweight Semantic Preference Engine v2 initialized! (Memory footprint < 10MB)")

    def _strip_ko_particle(self, word: str) -> str:
        """Strip trailing Korean grammatical particles to normalize noun stems."""
        for sfx in KO_PARTICLE_SUFFIXES:
            if len(word) >= len(sfx) + 2 and word.endswith(sfx):
                return word[:-len(sfx)]
        return word

    def _extract_tokens(self, text: str, title: str = "") -> List[str]:
        """
        Extract semantic words, normalized Korean stems, and character bi-grams.
        Title tokens receive 2x weight to prioritize headline concepts.
        """
        combined = f"{title} {title} {text}" if title else text
        if not combined:
            return []

        text_lower = combined.lower()
        # English words and technical terms
        raw_en = re.findall(r"[a-z0-9_]{2,}", text_lower)
        tokens = [w for w in raw_en if w not in EN_STOPWORDS and not w.isdigit()]

        # Korean nouns, stems, and semantic bi-grams
        hangul_blocks = re.findall(r"[\uac00-\ud7a3]+", text_lower)
        for h in hangul_blocks:
            if h in KO_STOPWORDS:
                continue
            stem = self._strip_ko_particle(h)
            if stem in KO_STOPWORDS:
                continue
            if len(stem) >= 2:
                tokens.append(stem)
                # Add character bi-grams for semantic morpheme matching
                for i in range(len(stem) - 1):
                    bg = stem[i:i + 2]
                    if bg not in KO_STOPWORDS:
                        tokens.append(bg)
        return tokens

    def _build_vocab_and_idf(self) -> Tuple[Dict[str, int], Optional[np.ndarray]]:
        """Build dynamic vocabulary and smooth IDF weights from active likes and dislikes."""
        vocab: Dict[str, int] = {}
        doc_freq: Counter = Counter()
        idx = 0
        with self.lock:
            all_items = self.preferences.get("likes", []) + self.preferences.get("dislikes", [])
            n_docs = len(all_items)
            for item in all_items:
                unique_tokens = set(item.get("tokens", []))
                for t in unique_tokens:
                    doc_freq[t] += 1
                    if t not in vocab:
                        vocab[t] = idx
                        idx += 1

        if not vocab:
            return {}, None

        idf = np.ones(len(vocab), dtype=np.float32)
        for t, i in vocab.items():
            df = doc_freq[t]
            # Smooth IDF so ubiquitous boilerplate tokens are downweighted
            idf[i] = float(math.log((1.0 + n_docs) / (1.0 + df)) + 1.0)
        return vocab, idf

    def _vectorize(
        self,
        tokens: List[str],
        vocab: Dict[str, int],
        idf: Optional[np.ndarray] = None,
    ) -> Optional[np.ndarray]:
        if not vocab or not tokens:
            return None
        counts = Counter(tokens)
        vec = np.zeros(len(vocab), dtype=np.float32)
        has_val = False
        for t, c in counts.items():
            if t in vocab:
                # Sublinear TF scaling (1 + log(tf)) * idf
                tf_val = 1.0 + math.log(float(c))
                vec[vocab[t]] = tf_val
                has_val = True
        if not has_val:
            return None
        if idf is not None:
            vec *= idf
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def _load_preferences(self) -> Dict[str, Any]:
        if os.path.exists(self.pref_file):
            try:
                with open(self.pref_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data.setdefault("likes", [])
                    data.setdefault("dislikes", [])
                    data.setdefault("blocked_log", [])
                    for item in data["likes"] + data["dislikes"]:
                        if "tokens" not in item or not item["tokens"]:
                            item["tokens"] = self._extract_tokens(
                                item.get("text", ""),
                                title=item.get("title", ""),
                            )
                    return data
            except Exception as e:
                logger.error(f"Failed to load preferences from {self.pref_file}: {e}")
        return {"likes": [], "dislikes": [], "blocked_log": []}

    def _save_preferences(self):
        with self.lock:
            try:
                with open(self.pref_file, "w", encoding="utf-8") as f:
                    json.dump(self.preferences, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Failed to save preferences to {self.pref_file}: {e}")

    def cache_content(self, content_id: str, text: str, title: str = "", category: str = "", url: str = ""):
        """Cache content metadata (including bilingual original + Korean AI briefing) for callbacks."""
        with self.cache_lock:
            existing = self.content_cache.get(content_id, {})
            merged_text = text
            if existing and existing.get("text") and existing["text"] not in text:
                merged_text = f"{existing['text']} {text}"

            self.content_cache[content_id] = {
                "id": content_id,
                "text": merged_text[:1500],
                "title": title or existing.get("title") or merged_text[:60],
                "category": category or existing.get("category", ""),
                "url": url or existing.get("url", ""),
                "cached_at": time.time(),
            }
            if len(self.content_cache) > 400:
                oldest_keys = sorted(
                    self.content_cache.keys(),
                    key=lambda k: self.content_cache[k]["cached_at"],
                )[:80]
                for k in oldest_keys:
                    self.content_cache.pop(k, None)

    def get_cached_content(self, content_id: str) -> Optional[Dict[str, Any]]:
        with self.cache_lock:
            return self.content_cache.get(content_id)

    def evaluate_content(
        self,
        text: str,
        category: str = "",
        matched_keywords: Optional[List[str]] = None,
        title: str = "",
    ) -> Tuple[bool, bool, str]:
        """
        Evaluate candidate content using IDF-weighted semantic vector space and relative margin rules.
        Returns:
            (should_block: bool, is_boosted: bool, reason: str)
        """
        # Hard Safety Rule 1: Emergency keywords ALWAYS bypass suppression!
        if matched_keywords and len(matched_keywords) > 0:
            return False, True, f"긴급 키워드({', '.join(matched_keywords)}) 매칭으로 무조건 전송"

        with self.lock:
            dislikes = list(self.preferences.get("dislikes", []))
            likes = list(self.preferences.get("likes", []))

        if not dislikes and not likes:
            return False, False, "중립 피드 정상 전송"

        vocab, idf = self._build_vocab_and_idf()
        if not vocab:
            return False, False, "중립 피드 정상 전송"

        cand_tokens = self._extract_tokens(text, title=title)
        cand_vec = self._vectorize(cand_tokens, vocab, idf)
        if cand_vec is None:
            return False, False, "중립 피드 정상 전송"

        max_dislike_sim = 0.0
        dislike_title = ""
        for d in dislikes:
            d_vec = self._vectorize(d.get("tokens", []), vocab, idf)
            if d_vec is not None:
                sim = float(np.dot(cand_vec, d_vec))
                if sim > max_dislike_sim:
                    max_dislike_sim = sim
                    dislike_title = d.get("title", "")

        max_like_sim = 0.0
        like_title = ""
        for l in likes:
            l_vec = self._vectorize(l.get("tokens", []), vocab, idf)
            if l_vec is not None:
                sim = float(np.dot(cand_vec, l_vec))
                if sim > max_like_sim:
                    max_like_sim = sim
                    like_title = l.get("title", "")

        # Defensive Rule 2: Relative Margin Check — If user likes this topic more than dislikes, boost it!
        if max_like_sim >= LIKE_BOOST_THRESHOLD and max_like_sim >= (max_dislike_sim - 0.03):
            reason = f"선호 주제 유사도({max_like_sim:.2f} >= {LIKE_BOOST_THRESHOLD}) 맞춤 추천 (유사글: '{like_title[:30]}')"
            return False, True, reason

        # Defensive Rule 3: Conservative Dislike Suppression (must exceed threshold AND beat like similarity by margin)
        if max_dislike_sim >= DISLIKE_SUPPRESS_THRESHOLD and max_dislike_sim > (max_like_sim + 0.05):
            log_title = title or text[:60]
            reason = f"비선호 주제 유사도({max_dislike_sim:.2f} >= {DISLIKE_SUPPRESS_THRESHOLD})로 차단 (유사글: '{dislike_title[:30]}')"
            self._log_blocked(log_title[:60], reason)
            return True, False, reason

        # Defensive Rule 4: Neutral Zone (Prevent filter bubble)
        return False, False, "중립 피드 정상 전송"

    def _log_blocked(self, title: str, reason: str):
        with self.lock:
            log = self.preferences.setdefault("blocked_log", [])
            log.append({
                "title": title,
                "reason": reason,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            if len(log) > 25:
                self.preferences["blocked_log"] = log[-25:]
        self._save_preferences()

    def record_feedback(
        self,
        content_id: str,
        feedback_type: str,
        fallback_text: str = "",
        fallback_title: str = "",
    ) -> Tuple[bool, str]:
        """
        Record user feedback ('like' or 'dislike') on a specific content item.
        Supports stateless recovery from Telegram message text if cache expired after container restart.
        """
        cached = self.get_cached_content(content_id)
        if not cached and fallback_text:
            # Stateless recovery from clicked Telegram message!
            clean_lines = [ln.strip() for ln in fallback_text.splitlines() if ln.strip()]
            inferred_title = fallback_title or (clean_lines[0] if clean_lines else fallback_text[:50])
            inferred_title = re.sub(r"^\[.*?\]\s*", "", inferred_title).strip()
            self.cache_content(
                content_id=content_id,
                text=fallback_text,
                title=inferred_title[:60],
                category="복원된 메시지",
            )
            cached = self.get_cached_content(content_id)

        if not cached:
            return False, "콘텐츠 정보를 찾을 수 없습니다 (만료됨)."

        text = cached.get("text", "")
        title = cached.get("title", text[:50])
        category = cached.get("category", "")
        tokens = self._extract_tokens(text, title=title)

        entry = {
            "id": content_id,
            "title": title,
            "category": category,
            "text": text[:350],
            "tokens": tokens,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        with self.lock:
            self.preferences["likes"] = [x for x in self.preferences.get("likes", []) if x["id"] != content_id]
            self.preferences["dislikes"] = [x for x in self.preferences.get("dislikes", []) if x["id"] != content_id]

            if feedback_type == "like":
                self.preferences.setdefault("likes", []).append(entry)
                if len(self.preferences["likes"]) > 60:
                    self.preferences["likes"] = self.preferences["likes"][-60:]
                msg = f"👍 '{title[:25]}...' 선호 피드백이 학습되었습니다."
            elif feedback_type == "dislike":
                self.preferences.setdefault("dislikes", []).append(entry)
                if len(self.preferences["dislikes"]) > 60:
                    self.preferences["dislikes"] = self.preferences["dislikes"][-60:]
                msg = f"👎 '{title[:25]}...' 비선호 피드백이 학습되어 유사한 소식을 필터링합니다."
            else:
                return False, "알 수 없는 피드백 유형입니다."

        self._save_preferences()
        return True, msg

    def undo_feedback(self, content_id: str) -> Tuple[bool, str]:
        """Undo previously recorded feedback for content_id (prevents accidental clicks)."""
        removed = False
        with self.lock:
            orig_likes = len(self.preferences.get("likes", []))
            self.preferences["likes"] = [x for x in self.preferences.get("likes", []) if x["id"] != content_id]
            if len(self.preferences["likes"]) < orig_likes:
                removed = True

            orig_dislikes = len(self.preferences.get("dislikes", []))
            self.preferences["dislikes"] = [x for x in self.preferences.get("dislikes", []) if x["id"] != content_id]
            if len(self.preferences["dislikes"]) < orig_dislikes:
                removed = True

        if removed:
            self._save_preferences()
            return True, "↩️ 피드백이 취소되었습니다."
        return False, "취소할 피드백 기록이 없습니다."

    def get_top_preferred_keywords(self, limit: int = 3) -> List[str]:
        """
        Extract top discriminative domain keywords from liked items for Dynamic Topic Expansion.
        Excludes stopwords, 2-char bigrams when full words exist, and disliked tokens.
        """
        with self.lock:
            likes = list(self.preferences.get("likes", []))
            dislikes = list(self.preferences.get("dislikes", []))

        if not likes:
            return []

        dislike_counter: Counter = Counter()
        for d in dislikes:
            for tok in set(d.get("tokens", [])):
                dislike_counter[tok] += 1

        like_counter: Counter = Counter()
        for l in likes:
            # Extract full words (length >= 2 Hangul or >= 3 English) from title + text
            raw_text = f"{l.get('title', '')} {l.get('title', '')} {l.get('text', '')}"
            ko_words = [
                self._strip_ko_particle(w)
                for w in re.findall(r"[\uac00-\ud7a3]{2,10}", raw_text)
            ]
            en_words = re.findall(r"[a-zA-Z]{3,15}", raw_text.lower())
            candidates = set()
            for w in ko_words:
                if len(w) >= 2 and w not in KO_STOPWORDS and w not in DEFAULT_BASE_QUERIES:
                    candidates.add(w)
            for w in en_words:
                if w not in EN_STOPWORDS and w not in DEFAULT_BASE_QUERIES:
                    candidates.add(w)

            for c in candidates:
                if dislike_counter[c] == 0:
                    like_counter[c] += 1

        top_keywords = [kw for kw, _ in like_counter.most_common(limit)]
        return top_keywords

    def export_backup_code(self) -> str:
        """Compress current likes and dislikes into a compact base64 string for persistent backup."""
        with self.lock:
            compact_payload = {
                "l": [
                    {
                        "id": x.get("id", ""),
                        "t": x.get("title", "")[:60],
                        "tx": x.get("text", "")[:200],
                        "tm": x.get("time", ""),
                    }
                    for x in self.preferences.get("likes", [])[-30:]
                ],
                "d": [
                    {
                        "id": x.get("id", ""),
                        "t": x.get("title", "")[:60],
                        "tx": x.get("text", "")[:200],
                        "tm": x.get("time", ""),
                    }
                    for x in self.preferences.get("dislikes", [])[-30:]
                ],
            }
        raw_bytes = json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        compressed = zlib.compress(raw_bytes, level=9)
        return base64.urlsafe_b64encode(compressed).decode("ascii")

    def import_backup_code(self, code: str) -> Tuple[bool, str]:
        """Restore likes and dislikes from a compact base64 backup string."""
        try:
            clean_code = code.strip()
            compressed = base64.urlsafe_b64decode(clean_code.encode("ascii"))
            raw_json = zlib.decompress(compressed).decode("utf-8")
            payload = json.loads(raw_json)

            restored_likes = []
            for item in payload.get("l", []):
                title = item.get("t", "")
                text = item.get("tx", "")
                restored_likes.append({
                    "id": item.get("id", f"bk_{len(restored_likes)}"),
                    "title": title,
                    "category": "복원됨",
                    "text": text,
                    "tokens": self._extract_tokens(text, title=title),
                    "time": item.get("tm", time.strftime("%Y-%m-%d %H:%M:%S")),
                })

            restored_dislikes = []
            for item in payload.get("d", []):
                title = item.get("t", "")
                text = item.get("tx", "")
                restored_dislikes.append({
                    "id": item.get("id", f"bkd_{len(restored_dislikes)}"),
                    "title": title,
                    "category": "복원됨",
                    "text": text,
                    "tokens": self._extract_tokens(text, title=title),
                    "time": item.get("tm", time.strftime("%Y-%m-%d %H:%M:%S")),
                })

            with self.lock:
                self.preferences["likes"] = restored_likes
                self.preferences["dislikes"] = restored_dislikes
            self._save_preferences()
            return True, f"선호 {len(restored_likes)}건, 비선호 {len(restored_dislikes)}건 복원 완료"
        except Exception as e:
            return False, f"백업 코드 디코딩 실패: {e}"

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            likes = list(self.preferences.get("likes", []))
            dislikes = list(self.preferences.get("dislikes", []))
            blocked = list(self.preferences.get("blocked_log", []))
        top_keywords = self.get_top_preferred_keywords(limit=4)
        return {
            "model_ready": self.model_ready,
            "like_count": len(likes),
            "dislike_count": len(dislikes),
            "recent_likes": [x.get("title", "") for x in likes[-3:]],
            "recent_dislikes": [x.get("title", "") for x in dislikes[-3:]],
            "recent_blocked": blocked[-3:],
            "expanded_keywords": top_keywords,
        }

    def reset(self):
        """Reset all learned preference vectors and logs."""
        with self.lock:
            self.preferences = {"likes": [], "dislikes": [], "blocked_log": []}
        self._save_preferences()
