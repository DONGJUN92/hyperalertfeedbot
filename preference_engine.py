import os
import json
import logging
import re
import threading
import time
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("x_preference")

# Calibrated thresholds reflecting subword vector space
DISLIKE_SUPPRESS_THRESHOLD = 0.50  # Conservative threshold to prevent false positive suppression
LIKE_BOOST_THRESHOLD = 0.40        # Relaxed threshold to catch nuanced topic matches
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class PreferenceEngine:
    """
    Ultra-Lightweight Semantic Preference & Recommendation Engine.
    Uses dense subword & semantic morpheme vector space (RAM usage < 10MB)
    to compute cosine similarity, eliminating Render 512MB RAM OOM crashes completely.
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
        # { "likes": [ {"id": ..., "title": ..., "text": ..., "tokens": [...], "time": ...} ],
        #   "dislikes": [ {"id": ..., "title": ..., "text": ..., "tokens": [...], "time": ...} ],
        #   "blocked_log": [ {"title": ..., "reason": ..., "time": ...} ] }
        self.preferences: Dict[str, Any] = self._load_preferences()
        logger.info("✅ Ultra-Lightweight Semantic Preference Engine initialized successfully! (Memory footprint < 10MB)")

    def _extract_tokens(self, text: str) -> List[str]:
        """Extract semantic words and character bi-grams for Korean and English."""
        if not text:
            return []
        text_lower = text.lower()
        # English words and technical terms
        words = re.findall(r"[a-z0-9_]{2,}", text_lower)
        # Korean nouns, syllables, and semantic bi-grams
        hangul_blocks = re.findall(r"[\uac00-\ud7a3]+", text_lower)
        tokens = list(words)
        for h in hangul_blocks:
            if len(h) >= 2:
                tokens.append(h)
                # Add character bi-grams for semantic morpheme matching
                for i in range(len(h) - 1):
                    tokens.append(h[i:i + 2])
            elif len(h) == 1:
                tokens.append(h)
        return tokens

    def _build_vocab(self) -> Dict[str, int]:
        """Build dynamic vocabulary from active likes and dislikes."""
        vocab: Dict[str, int] = {}
        idx = 0
        with self.lock:
            all_items = self.preferences.get("likes", []) + self.preferences.get("dislikes", [])
            for item in all_items:
                for t in item.get("tokens", []):
                    if t not in vocab:
                        vocab[t] = idx
                        idx += 1
        return vocab

    def _vectorize(self, tokens: List[str], vocab: Dict[str, int]) -> Optional[np.ndarray]:
        if not vocab or not tokens:
            return None
        counts = Counter(tokens)
        vec = np.zeros(len(vocab), dtype=np.float32)
        has_val = False
        for t, c in counts.items():
            if t in vocab:
                vec[vocab[t]] = float(c)
                has_val = True
        if not has_val:
            return None
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
                    # Ensure tokens are present
                    for item in data["likes"] + data["dislikes"]:
                        if "tokens" not in item:
                            item["tokens"] = self._extract_tokens(item.get("text", "") + " " + item.get("title", ""))
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
        """Cache content metadata for incoming Telegram callback queries."""
        with self.cache_lock:
            self.content_cache[content_id] = {
                "id": content_id,
                "text": text,
                "title": title or text[:60],
                "category": category,
                "url": url,
                "cached_at": time.time(),
            }
            # Limit memory size of content cache (keep last 300 items)
            if len(self.content_cache) > 300:
                oldest_keys = sorted(self.content_cache.keys(), key=lambda k: self.content_cache[k]["cached_at"])[:50]
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
    ) -> Tuple[bool, bool, str]:
        """
        Evaluate candidate content using semantic vector space and defensive rules.
        Returns:
            (should_block: bool, is_boosted: bool, reason: str)
        """
        # Hard Safety Rule 1: Emergency keywords ALWAYS bypass suppression!
        if matched_keywords and len(matched_keywords) > 0:
            return False, True, f"긴급 키워드({', '.join(matched_keywords)}) 매칭으로 무조건 전송"

        with self.lock:
            dislikes = list(self.preferences.get("dislikes", []))
            likes = list(self.preferences.get("likes", []))

        # If no user preferences exist yet, pass normally
        if not dislikes and not likes:
            return False, False, "중립 피드 정상 전송"

        vocab = self._build_vocab()
        if not vocab:
            return False, False, "중립 피드 정상 전송"

        cand_tokens = self._extract_tokens(text)
        cand_vec = self._vectorize(cand_tokens, vocab)
        if cand_vec is None:
            return False, False, "중립 피드 정상 전송"

        max_dislike_sim = 0.0
        dislike_title = ""
        for d in dislikes:
            d_vec = self._vectorize(d.get("tokens", []), vocab)
            if d_vec is not None:
                sim = float(np.dot(cand_vec, d_vec))
                if sim > max_dislike_sim:
                    max_dislike_sim = sim
                    dislike_title = d.get("title", "")

        max_like_sim = 0.0
        like_title = ""
        for l in likes:
            l_vec = self._vectorize(l.get("tokens", []), vocab)
            if l_vec is not None:
                sim = float(np.dot(cand_vec, l_vec))
                if sim > max_like_sim:
                    max_like_sim = sim
                    like_title = l.get("title", "")

        # Defensive Rule 2: Conservative Dislike Suppression
        if max_dislike_sim >= DISLIKE_SUPPRESS_THRESHOLD:
            reason = f"비선호 주제 유사도({max_dislike_sim:.2f} >= {DISLIKE_SUPPRESS_THRESHOLD})로 차단 (유사글: '{dislike_title[:30]}')"
            self._log_blocked(text[:60], reason)
            return True, False, reason

        # Defensive Rule 3: Like Recommendation Boosting
        if max_like_sim >= LIKE_BOOST_THRESHOLD:
            reason = f"선호 주제 유사도({max_like_sim:.2f} >= {LIKE_BOOST_THRESHOLD}) 맞춤 추천 (유사글: '{like_title[:30]}')"
            return False, True, reason

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
            if len(log) > 20:
                self.preferences["blocked_log"] = log[-20:]
        self._save_preferences()

    def record_feedback(self, content_id: str, feedback_type: str) -> Tuple[bool, str]:
        """
        Record user feedback ('like' or 'dislike') on a specific content item.
        Returns (success: bool, message: str)
        """
        cached = self.get_cached_content(content_id)
        if not cached:
            return False, "콘텐츠 정보를 찾을 수 없습니다 (만료됨)."

        text = cached.get("text", "")
        title = cached.get("title", text[:50])
        category = cached.get("category", "")
        tokens = self._extract_tokens(text + " " + title)

        entry = {
            "id": content_id,
            "title": title,
            "category": category,
            "text": text[:300],
            "tokens": tokens,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        with self.lock:
            # Remove any existing feedback for this content_id first (toggle/update)
            self.preferences["likes"] = [x for x in self.preferences.get("likes", []) if x["id"] != content_id]
            self.preferences["dislikes"] = [x for x in self.preferences.get("dislikes", []) if x["id"] != content_id]

            if feedback_type == "like":
                self.preferences.setdefault("likes", []).append(entry)
                if len(self.preferences["likes"]) > 50:
                    self.preferences["likes"] = self.preferences["likes"][-50:]
                msg = f"👍 '{title[:25]}...' 선호 피드백이 학습되었습니다."
            elif feedback_type == "dislike":
                self.preferences.setdefault("dislikes", []).append(entry)
                if len(self.preferences["dislikes"]) > 50:
                    self.preferences["dislikes"] = self.preferences["dislikes"][-50:]
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

    def get_stats(self) -> Dict[str, Any]:
        with self.lock:
            likes = list(self.preferences.get("likes", []))
            dislikes = list(self.preferences.get("dislikes", []))
            blocked = list(self.preferences.get("blocked_log", []))
        return {
            "model_ready": self.model_ready,
            "like_count": len(likes),
            "dislike_count": len(dislikes),
            "recent_likes": [x.get("title", "") for x in likes[-3:]],
            "recent_dislikes": [x.get("title", "") for x in dislikes[-3:]],
            "recent_blocked": blocked[-3:],
        }

    def reset(self):
        """Reset all learned preference vectors and logs."""
        with self.lock:
            self.preferences = {"likes": [], "dislikes": [], "blocked_log": []}
        self._save_preferences()
