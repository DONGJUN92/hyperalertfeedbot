import os
import json
import logging
import re
import threading
import time
import urllib.request
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("x_preference")

# Calibrated thresholds reflecting empirical semantic similarity distribution
DISLIKE_SUPPRESS_THRESHOLD = 0.62  # Conservative threshold to prevent false positive suppression
LIKE_BOOST_THRESHOLD = 0.48        # Relaxed threshold to catch nuanced topic matches
MODEL_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


class PreferenceEngine:
    """
    Lightweight Deep Learning Preference & Recommendation Engine.
    Uses an INT8 quantized multilingual Transformer ONNX model (paraphrase-multilingual-MiniLM-L12-v2)
    to compute 384-dimensional semantic embedding vectors, learning user likes and dislikes in real time.
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR, model_dir: str = DEFAULT_MODEL_DIR):
        self.data_dir = data_dir
        self.model_dir = model_dir
        self.pref_file = os.path.join(self.data_dir, "preferences.json")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.model_dir, exist_ok=True)

        self.lock = threading.Lock()
        self.model_ready = False
        self.tokenizer = None
        self.session = None

        # Content cache mapping short content_id -> metadata
        # (Needed because Telegram callback_data has strict 64-byte limit)
        self.content_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()

        # Feedback store
        # { "likes": [ {"id": ..., "text": ..., "vector": [...], "time": ...} ],
        #   "dislikes": [ {"id": ..., "text": ..., "vector": [...], "time": ...} ],
        #   "blocked_log": [ {"title": ..., "reason": ..., "time": ...} ] }
        self.preferences: Dict[str, Any] = self._load_preferences()

        # Start asynchronous background loader for deep learning model
        # This guarantees HTTP port 8000 health check starts instantly with 0ms delay!
        self.init_thread = threading.Thread(target=self._init_model_async, daemon=True)
        self.init_thread.start()

    def _load_preferences(self) -> Dict[str, Any]:
        if os.path.exists(self.pref_file):
            try:
                with open(self.pref_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data.setdefault("likes", [])
                    data.setdefault("dislikes", [])
                    data.setdefault("blocked_log", [])
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

    def _init_model_async(self):
        """Download and initialize ONNX multilingual model in background thread."""
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tok_path = os.path.join(self.model_dir, "tokenizer.json")
            onnx_path = os.path.join(self.model_dir, "model_quint8.onnx")

            # 1. Download tokenizer if missing
            if not os.path.exists(tok_path) or os.path.getsize(tok_path) < 1000:
                logger.info("Downloading multilingual tokenizer...")
                tok_url = f"https://huggingface.co/{MODEL_REPO}/resolve/main/tokenizer.json"
                req = urllib.request.Request(tok_url, headers={"User-Agent": "FeedBot/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(tok_path, "wb") as f:
                    f.write(resp.read())

            # 2. Download ONNX quint8 model if missing
            if not os.path.exists(onnx_path) or os.path.getsize(onnx_path) < 1000:
                logger.info("Downloading quantized ONNX multilingual model (~112MB)...")
                onnx_url = f"https://huggingface.co/{MODEL_REPO}/resolve/main/onnx/model_quint8_avx2.onnx"
                req = urllib.request.Request(onnx_url, headers={"User-Agent": "FeedBot/1.0"})
                with urllib.request.urlopen(req, timeout=60) as resp, open(onnx_path, "wb") as f:
                    f.write(resp.read())

            logger.info("Loading tokenizer and ONNX inference session...")
            self.tokenizer = Tokenizer.from_file(tok_path)
            self.tokenizer.enable_truncation(max_length=128)
            self.tokenizer.enable_padding(length=128)

            # Session options optimized for low-memory cloud container (Render 512MB RAM)
            opts = ort.SessionOptions()
            opts.enable_cpu_mem_arena = False
            opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            opts.intra_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC

            self.session = ort.InferenceSession(onnx_path, opts, providers=["CPUExecutionProvider"])
            self.model_ready = True
            logger.info("✅ Deep Learning Multilingual Preference Engine initialized successfully!")

            # Re-vectorize existing feedback if any vectors were missing
            self._ensure_existing_vectors()

        except Exception as e:
            logger.error(f"Failed to initialize Deep Learning ONNX model: {e}", exc_info=True)

    def _ensure_existing_vectors(self):
        """Ensure all stored likes/dislikes have precomputed 384-d vectors."""
        updated = False
        with self.lock:
            for item in self.preferences.get("likes", []):
                if "vector" not in item:
                    v = self.compute_embedding(item.get("text", ""))
                    if v is not None:
                        item["vector"] = v
                        updated = True
            for item in self.preferences.get("dislikes", []):
                if "vector" not in item:
                    v = self.compute_embedding(item.get("text", ""))
                    if v is not None:
                        item["vector"] = v
                        updated = True
        if updated:
            self._save_preferences()

    def compute_embedding(self, text: str) -> Optional[List[float]]:
        """Compute 384-dimensional normalized semantic vector using deep learning model."""
        if not self.model_ready or not self.session or not self.tokenizer or not text:
            return None
        try:
            import numpy as np

            # Clean text for embedding
            clean = " ".join(text.split())[:500]
            enc = self.tokenizer.encode(clean)

            input_ids = np.array([enc.ids], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask], dtype=np.int64)
            token_type_ids = np.zeros_like(input_ids)

            inputs = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            }
            outputs = self.session.run(None, inputs)
            last_hidden = outputs[0]  # shape: (1, 128, 384)

            # Mean pooling with attention mask
            mask_exp = np.expand_dims(attention_mask, -1)
            sum_embeddings = np.sum(last_hidden * mask_exp, axis=1)
            sum_mask = np.clip(mask_exp.sum(axis=1), a_min=1e-9, a_max=None)
            emb = sum_embeddings / sum_mask

            # L2 normalize
            norm = np.linalg.norm(emb, axis=1, keepdims=True)
            norm_emb = emb / norm
            return [round(float(x), 5) for x in norm_emb[0]]
        except Exception as e:
            logger.debug(f"Embedding computation error: {e}")
            return None

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
        Evaluate candidate content using deep learning embeddings and defensive rules.
        Returns:
            (should_block: bool, is_boosted: bool, reason: str)
        """
        # Hard Safety Rule 1: Emergency keywords ALWAYS bypass suppression!
        if matched_keywords and len(matched_keywords) > 0:
            return False, True, f"긴급 키워드({', '.join(matched_keywords)}) 매칭으로 무조건 전송"

        # If model is still loading or no text, allow normal pass
        if not self.model_ready or not text:
            return False, False, "중립 (기본 전송)"

        v = self.compute_embedding(text)
        if v is None:
            return False, False, "중립 (임베딩 미생성)"

        import numpy as np
        v_np = np.array(v)

        max_dislike_sim = -1.0
        dislike_title = ""
        max_like_sim = -1.0
        like_title = ""

        with self.lock:
            dislikes = list(self.preferences.get("dislikes", []))
            likes = list(self.preferences.get("likes", []))

        # Check dislike similarities
        for d in dislikes:
            d_vec = d.get("vector")
            if d_vec:
                sim = float(np.dot(v_np, np.array(d_vec)))
                if sim > max_dislike_sim:
                    max_dislike_sim = sim
                    dislike_title = d.get("title", "")

        # Check like similarities
        for l in likes:
            l_vec = l.get("vector")
            if l_vec:
                sim = float(np.dot(v_np, np.array(l_vec)))
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

        # Compute embedding vector
        vec = self.compute_embedding(text)

        entry = {
            "id": content_id,
            "title": title,
            "category": category,
            "text": text[:300],
            "vector": vec,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        with self.lock:
            # Remove any existing feedback for this content_id first (toggle/update)
            self.preferences["likes"] = [x for x in self.preferences.get("likes", []) if x["id"] != content_id]
            self.preferences["dislikes"] = [x for x in self.preferences.get("dislikes", []) if x["id"] != content_id]

            if feedback_type == "like":
                self.preferences.setdefault("likes", []).append(entry)
                # Keep max 50 recent likes to avoid memory blowup
                if len(self.preferences["likes"]) > 50:
                    self.preferences["likes"] = self.preferences["likes"][-50:]
                msg = f"👍 '{title[:25]}...' 선호 피드백이 학습되었습니다."
            elif feedback_type == "dislike":
                self.preferences.setdefault("dislikes", []).append(entry)
                # Keep max 50 recent dislikes
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
