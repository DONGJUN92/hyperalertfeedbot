import logging
import os
import re
import threading
import time
import requests
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("x_summarizer")

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_FALLBACK_MODEL = "openrouter/free"
EXCLUDE_KEYWORDS = [
    "safety", "guard", "moderation", "code", "embed", "audio", "whisper", "vision-only", "clip", "preview", "reasoning"
]


class OpenRouterModelSelector:
    """
    Dynamically discovers, scores, and probes OpenRouter's free (:free) models.
    Selects the most capable and responsive model on a 1-hour recurring schedule.
    """
    def __init__(self, api_key: str = "", eval_interval_seconds: int = 3600):
        self.api_key = api_key.strip()
        self.eval_interval = eval_interval_seconds
        self.lock = threading.Lock()
        self.active_model = DEFAULT_FALLBACK_MODEL
        self.last_latency = 0.0
        self.last_evaluated_at: Optional[float] = None
        self.candidate_summary: List[Dict[str, Any]] = []
        self.running = True
        self._thread: Optional[threading.Thread] = None

    def set_api_key(self, api_key: str):
        with self.lock:
            old_key = self.api_key
            self.api_key = api_key.strip()
            # If key changed from empty to valid, trigger an immediate evaluation
            if not old_key and self.api_key:
                threading.Thread(target=self.evaluate_and_select, daemon=True).start()

    def get_active_model(self) -> str:
        with self.lock:
            return self.active_model

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "active_model": self.active_model,
                "latency_seconds": self.last_latency,
                "last_evaluated_at": self.last_evaluated_at,
                "candidates_count": len(self.candidate_summary),
                "candidates": list(self.candidate_summary[:5]),
            }

    def fetch_free_candidates(self) -> List[Tuple[int, str, str]]:
        """Fetch all free models from OpenRouter and score them by relative capability."""
        headers = {"User-Agent": "AlphaIntelligenceBot/2.0"}
        try:
            resp = requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=12)
            if resp.status_code != 200:
                logger.warning(f"Failed to fetch OpenRouter models: HTTP {resp.status_code}")
                return [(50, DEFAULT_FALLBACK_MODEL, "Free Models Router")]

            data = resp.json().get("data", [])
            ranked: List[Tuple[int, str, str]] = []

            for m in data:
                mid = m.get("id", "")
                pricing = m.get("pricing", {})
                is_free = ":free" in mid or (pricing.get("prompt") == "0" and pricing.get("completion") == "0")
                if not is_free:
                    continue

                # Exclude safety/moderation/code filters
                mid_lower = mid.lower()
                if any(ex in mid_lower for ex in EXCLUDE_KEYWORDS):
                    continue

                # Compute relative capability score
                score = 50
                # Parameter size scoring
                if "550b" in mid_lower:
                    score += 50
                elif "120b" in mid_lower:
                    score += 40
                elif "70b" in mid_lower or "72b" in mid_lower:
                    score += 30
                elif "31b" in mid_lower or "32b" in mid_lower:
                    score += 25
                elif "26b" in mid_lower or "27b" in mid_lower:
                    score += 20
                elif "8b" in mid_lower or "9b" in mid_lower:
                    score += 10

                # High quality model family bonuses
                if any(f in mid_lower for f in ["gemma", "llama", "qwen", "mistral"]):
                    score += 15
                if "nemotron" in mid_lower:
                    score += 15
                if "fin" in mid_lower:  # Financial / Economics specialization bonus
                    score += 10

                # Context length bonus
                ctx = m.get("context_length") or 0
                if ctx >= 65536:
                    score += 5

                name = m.get("name", mid)
                ranked.append((score, mid, name))

            # Sort descending by score
            ranked.sort(key=lambda x: x[0], reverse=True)

            # Ensure openrouter/free is present in candidates
            if not any(r[1] == DEFAULT_FALLBACK_MODEL for r in ranked):
                ranked.append((45, DEFAULT_FALLBACK_MODEL, "Free Models Router"))

            return ranked
        except Exception as e:
            logger.error(f"Error fetching OpenRouter models: {e}")
            return [(50, DEFAULT_FALLBACK_MODEL, "Free Models Router")]

    def probe_model(self, model_id: str) -> Optional[float]:
        """Perform a minimal live probe call to verify smoothness and measure latency."""
        if not self.api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/DONGJUN92/hyperalertfeedbot",
            "X-Title": "Alpha Intelligence Feed Bot",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "1+1="}],
            "max_tokens": 5,
            "temperature": 0.0,
        }

        start_t = time.time()
        try:
            resp = requests.post(OPENROUTER_CHAT_URL, json=payload, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("choices"):
                    latency = round(time.time() - start_t, 2)
                    return latency
            else:
                logger.debug(f"OpenRouter probe for '{model_id}' failed: HTTP {resp.status_code} ({resp.text[:120]})")
        except Exception as e:
            logger.debug(f"OpenRouter probe exception for '{model_id}': {e}")

        return None

    def evaluate_and_select(self) -> str:
        """Evaluate top free models, test responsiveness, and select the optimal active model."""
        logger.info("[OpenRouter Auto-Selector] Starting hourly free model evaluation...")
        candidates = self.fetch_free_candidates()
        logger.info(f"[OpenRouter Auto-Selector] Found {len(candidates)} candidate free models.")

        selected_model = DEFAULT_FALLBACK_MODEL
        selected_latency = 0.0
        summary_records = []

        if self.api_key:
            # Probe top 6 candidates
            top_pool = candidates[:6]
            found = False
            for score, mid, name in top_pool:
                logger.info(f"[OpenRouter Auto-Selector] Probing '{mid}' (Score: {score})...")
                latency = self.probe_model(mid)
                status_str = f"OK ({latency}s)" if latency is not None else "FAIL/BUSY"
                summary_records.append({"id": mid, "name": name, "score": score, "status": status_str, "latency": latency})

                if latency is not None and not found:
                    selected_model = mid
                    selected_latency = latency
                    found = True
                    logger.info(f"⭐️ [OpenRouter Auto-Selector] Selected active model: {mid} (Latency: {latency}s)")

            if not found:
                logger.warning(f"[OpenRouter Auto-Selector] Top models failed probe, fallback to {DEFAULT_FALLBACK_MODEL}")
                selected_model = DEFAULT_FALLBACK_MODEL
        else:
            # If no API key yet, select top scored model as nominal active
            if candidates:
                selected_model = candidates[0][1]
                for score, mid, name in candidates[:5]:
                    summary_records.append({"id": mid, "name": name, "score": score, "status": "KEY_NEEDED", "latency": None})

        with self.lock:
            self.active_model = selected_model
            self.last_latency = selected_latency
            self.last_evaluated_at = time.time()
            self.candidate_summary = summary_records

        return selected_model

    def start_hourly_loop(self):
        """Start the background daemon thread that evaluates models every 1 hour."""
        if self._thread and self._thread.is_alive():
            return

        def _loop():
            # Initial run
            self.evaluate_and_select()
            while self.running:
                time.sleep(self.eval_interval)
                try:
                    self.evaluate_and_select()
                except Exception as e:
                    logger.error(f"Error in hourly model evaluation: {e}")

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        logger.info(f"OpenRouter 1-hour model evaluator thread started (Interval: {self.eval_interval}s).")


def clean_ai_summary(text: str) -> str:
    """
    Post-process AI output to guarantee crystal-clear, minimal 3-bullet output.
    1. Removes any <think>...</think> blocks
    2. Strips 'Here's a thinking process' or chain-of-thought analysis
    3. Removes markdown symbols (**, #, `) and bracket noise ([핵심 결론] 등)
    4. Normalizes to:
       • 핵심: ...
       • 배경: ...
       • 전망: ...
    """
    if not text:
        return ""

    # 1. Remove XML-style think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # 2. Strip thinking process preambles
    lower_t = text.lower()
    if any(k in lower_t for k in ["thinking process", "analyze user request", "here is a thinking", "here's a thinking"]):
        idx = text.find("•")
        if idx != -1:
            text = text[idx:]
        else:
            candidate_lines = []
            for l in text.split("\n"):
                ls = l.strip()
                if ls.startswith("•") or re.match(r"^[-*]\s*\[", ls) or re.match(r"^\d+\.\s*\[", ls):
                    candidate_lines.append(ls)
            if candidate_lines:
                text = "\n".join(candidate_lines)

    lines = []
    labels = ["핵심:", "배경:", "전망:"]
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip lingering thinking/meta tokens
        if any(line.lower().startswith(p) for p in [
            "here", "thinking", "analyze", "role:", "field:", "title:", "excerpt:", "format", "time constraint"
        ]):
            continue

        # Strip markdown symbols
        line = line.replace("**", "").replace("*", "").replace("`", "").replace("#", "").strip()
        line = re.sub(r"^[-•\d\.]+\s*", "", line).strip()

        # Clean bracket tags
        line = re.sub(r"^\[핵심\s*결론\]\s*:?", "", line)
        line = re.sub(r"^\[세부\s*내용\]\s*:?", "", line)
        line = re.sub(r"^\[영향\s*및?\s*시사점\]\s*:?", "", line)
        line = re.sub(r"^\[.*?\]\s*:?", "", line).strip()

        # Strip label if model already generated it
        for lb in labels:
            if line.startswith(lb):
                line = line[len(lb):].strip()

        if line:
            curr_label = labels[len(lines)] if len(lines) < len(labels) else "참고:"
            lines.append(f"• {curr_label} {line}")
            if len(lines) >= 3:
                break

    return "\n".join(lines)


class AISummarizer:
    def __init__(self, api_key: Optional[str] = None):
        key = (api_key or os.environ.get("OPENROUTER_API_KEY", "")).strip()
        self.selector = OpenRouterModelSelector(api_key=key, eval_interval_seconds=3600)
        self.selector.start_hourly_loop()

    @property
    def api_key(self) -> str:
        return self.selector.api_key

    def update_api_key(self, api_key: str):
        self.selector.set_api_key(api_key)

    def get_status(self) -> Dict[str, Any]:
        return self.selector.get_status()

    def force_evaluate(self) -> str:
        return self.selector.evaluate_and_select()

    def summarize(self, title: str, content: str, category: str = "") -> Optional[str]:
        """
        Summarize news article or ArXiv paper into a concise 3-bullet insight using OpenRouter.
        Returns formatted string for Telegram or None if fallback needed.
        """
        if not self.api_key or self.api_key.startswith("YOUR_"):
            return None

        # Prepare context
        clean_content = (content or "").strip()
        if clean_content and clean_content != title.strip() and len(clean_content) > 30:
            body_text = f"제목: {title}\n원문 발췌:\n{clean_content}"
        else:
            body_text = f"기사 제목 및 속보: {title}"

        system_prompt = (
            "너는 블룸버그 터미널의 수석 인텔리전스 에디터다.\n"
            "의사결정자가 3초 만에 핵심을 직관적으로 파악할 수 있도록 뉴스/논문을 핵심 위주로 극도로 간결하게 브리핑한다.\n\n"
            "[원칙]\n"
            "1. 절대 생각 과정(Thinking process)이나 분석 과정, 서론, 결론, 해설을 쓰지 마라.\n"
            "2. 마크다운 기호(**, #, 따옴표 등)나 대괄호([])를 일체 사용하지 마라.\n"
            "3. 오직 '• 핵심:', '• 배경:', '• 전망:'으로 시작하는 정확히 3줄의 한국어 불릿만 출력하라.\n"
            "4. 각 줄은 1문장으로 군더더기 없이 단정적인 어조로 작성하라."
        )

        user_prompt = (
            f"분야: {category}\n"
            f"{body_text}\n\n"
            "위 내용을 바탕으로 아래 예시와 완전히 동일한 형식의 3줄 브리핑을 한국어로 작성하라.\n\n"
            "[출력 예시]\n"
            "• 핵심: (가장 중요한 사실 1문장)\n"
            "• 배경: (구체적 원인, 수치, 관련 대상 1문장)\n"
            "• 전망: (시장, 정책, 산업에 미칠 파급효과 1문장)"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/DONGJUN92/hyperalertfeedbot",
            "X-Title": "Alpha Intelligence Feed Bot",
            "Content-Type": "application/json",
        }

        # Try active model first, then fallback to openrouter/free if failed
        active_model = self.selector.get_active_model()
        attempt_models = [active_model]
        if DEFAULT_FALLBACK_MODEL not in attempt_models:
            attempt_models.append(DEFAULT_FALLBACK_MODEL)

        for mod in attempt_models:
            payload = {
                "model": mod,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 450,
                "include_reasoning": False,
            }
            try:
                resp = requests.post(OPENROUTER_CHAT_URL, json=payload, headers=headers, timeout=16)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_text = choices[0].get("message", {}).get("content", "").strip()
                        cleaned_text = clean_ai_summary(raw_text)
                        if cleaned_text and len(cleaned_text.split("\n")) >= 2:
                            return cleaned_text
                else:
                    logger.warning(f"OpenRouter ({mod}) returned HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.error(f"OpenRouter summarization exception on {mod}: {e}")

        return None


if __name__ == "__main__":
    summarizer = AISummarizer()
    print("Summarizer initialized. Initial active model:", summarizer.selector.get_active_model())
