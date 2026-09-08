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
    Post-process AI output to guarantee a natural, conversational executive briefing.
    1. Removes <think>...</think> and unclosed think/thought tags.
    2. Strips thinking process preambles and extracts Korean text.
    3. Strips greetings ('안녕하세요', 'Here is a briefing') and sign-offs.
    4. Strips markdown asterisks, hashes, backticks, and bracket tags.
    5. Formats into clean, readable briefing paragraphs separated by blank lines.
    """
    if not text:
        return ""

    # 1. Remove XML-style think/thought blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "<think>" in text:
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "<thought>" in text:
        text = re.sub(r"<thought>.*", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Strip thinking process preambles
    ko_match = re.search(r"[\uac00-\ud7a3]", text)
    if ko_match:
        preamble = text[:ko_match.start()].lower()
        if any(p in preamble for p in ["thinking", "analyze", "here", "role:", "field:", "prompt", "user:"]):
            text = text[ko_match.start():]

    lines = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue

        # Skip English meta/thinking lines
        if any(line.lower().startswith(p) for p in [
            "here's", "here is", "thinking process", "analyze user request", "role:", "field:", "title:", "excerpt:", "format", "time constraint", "note:"
        ]):
            continue

        # Strip greetings & meta introductions
        if any(line.startswith(g) for g in [
            "안녕하세요", "안녕하십니까", "브리핑을 시작하겠습니다", "다음은 브리핑", "보고서 요약입니다", "이상 브리핑", "감사합니다", "요약 브리핑:"
        ]):
            continue

        # Strip markdown symbols
        line = line.replace("**", "").replace("*", "").replace("`", "").replace("#", "").strip()

        # Strip artificial bracket tags or bullet headers at line starts
        line = re.sub(r"^\[.*?\]\s*:?", "", line).strip()
        line = re.sub(r"^(핵심|배경|전망|시사점|요약)\s*:\s*", "", line).strip()
        line = re.sub(r"^[-•\d\.]+\s*", "", line).strip()

        if line:
            lines.append(line)

    cleaned = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def get_env_api_key() -> str:
    """Auto-detect OpenRouter API key across environment variable name variations."""
    # 1. Explicit variable names
    for k in [
        "OPENROUTER_API_KEY",
        "OPENROUTER_KEY",
        "OPEN_ROUTER_API_KEY",
        "OPEN_ROUTER_KEY",
        "OPENROUTER",
        "OPENROUTER_TOKEN",
        "OR_API_KEY",
        "OR_KEY",
        "API_KEY",
    ]:
        val = os.environ.get(k)
        if val and val.strip() and not val.startswith("YOUR_"):
            return val.strip()

    # 2. Match any environment variable whose value starts with "sk-or-"
    for k, v in os.environ.items():
        if isinstance(v, str) and v.strip().startswith("sk-or-"):
            return v.strip()

    # 3. Match any environment variable with "openrouter" in key name
    for k, v in os.environ.items():
        if "openrouter" in k.lower():
            if v and v.strip() and not v.startswith("YOUR_"):
                return v.strip()

    return ""


def validate_api_key(api_key: str) -> Tuple[bool, str]:
    """Live-probes an API key against OpenRouter auth endpoint."""
    if not api_key:
        return False, "OpenRouter API 키가 등록되지 않았습니다."
    key = api_key.strip()
    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            label = data.get("label", "OpenRouter Key")
            return True, f"OpenRouter 정상 인증 🟢 ({label})"
        elif resp.status_code == 401:
            return False, "OpenRouter 401 Unauthorized: API 키가 올바른지 확인해주세요."
        else:
            # For 429 or other non-401 status, do not block the key
            return True, f"OpenRouter 키 확인 (HTTP {resp.status_code})"
    except Exception as e:
        # On network failure, do not block
        return True, f"OpenRouter 키 형식 확인 (연결 대기: {e})"


class AISummarizer:
    def __init__(self, api_key: Optional[str] = None):
        key = (api_key or get_env_api_key()).strip()
        self.selector = OpenRouterModelSelector(api_key=key, eval_interval_seconds=3600)
        self.last_error: Optional[str] = None
        self.selector.start_hourly_loop()

    @property
    def api_key(self) -> str:
        return self.selector.api_key

    def update_api_key(self, api_key: str):
        self.selector.set_api_key(api_key)
        self.last_error = None
        self.selector.start_hourly_loop()

    def validate_current_key(self) -> Tuple[bool, str]:
        return validate_api_key(self.api_key)

    def get_active_model(self) -> str:
        return self.selector.get_active_model()

    def get_status(self) -> Dict[str, Any]:
        status = self.selector.get_status()
        status["provider"] = "OpenRouter"
        status["last_error"] = self.last_error
        return status

    def force_evaluate(self) -> str:
        return self.selector.evaluate_and_select()

    def summarize_tweet(self, author: str, username: str, text: str) -> Optional[str]:
        """Summarize and translate foreign/English VIP tweet into a Korean executive briefing."""
        clean_text = (text or "").strip()
        if not clean_text:
            return None
        return self.summarize(
            title=f"@{username} ({author}) VIP 공식 발언",
            content=clean_text,
            category="VIP 트윗 브리핑",
        )

    def summarize(self, title: str, content: str, category: str = "") -> Optional[str]:
        """
        Summarize tweet, news, or ArXiv paper into a rich conversational briefing using OpenRouter free models.
        """
        curr_key = self.api_key or get_env_api_key()
        if not curr_key or curr_key.startswith("YOUR_"):
            logger.warning("[AISummarizer] Cannot summarize: OpenRouter API key is not configured.")
            self.last_error = "OpenRouter API 키 미설정"
            return None

        # Ensure selector has key
        if not self.selector.api_key and curr_key:
            self.selector.set_api_key(curr_key)

        # Prepare context
        clean_content = (content or "").strip()
        if clean_content and clean_content != title.strip() and len(clean_content) > 30:
            body_text = f"제목/출처: {title}\n원문 내용:\n{clean_content}"
        else:
            body_text = f"제목 및 내용: {title}\n{clean_content}"

        system_prompt = (
            "너는 최고위 의사결정권자(경영진·투자자)에게 실시간 인텔리전스를 1:1로 직접 구두 보고하는 전담 수석 분석관이다.\n"
            "영문 또는 국문 원문의 모든 중요 사실, 배경, 핵심 인물/기업, 구체적 수치, 향후 시장·산업·정책적 파급효과를 절대로 누락하지 말고, "
            "글자 수 제한 없이 충분히 깊이 있고 상세하게 100% 정중한 한국어 구어체(~했습니다, ~상황입니다, ~전망됩니다) 완결 문장으로 브리핑하라.\n\n"
            "[작성 수칙 - 엄격 준수]\n"
            "1. 반드시 순수 한국어로만 작성하라. 영어 원문이더라도 완벽한 한국어로 번역 및 재해석하여 설명하라.\n"
            "2. 서론 인사('안녕하세요', '브리핑입니다' 등)나 맺음말, 분석 과정(Thinking process, CoT), 메타 발언을 일체 쓰지 말고 본론 브리핑 문장으로 즉시 시작하라.\n"
            "3. 마크다운 기호(**, #, 따옴표)나 인위적인 불릿 머리말(•, [핵심] 등)을 쓰지 말고, 2~4개의 단락으로 자연스럽게 나누어 작성하라:\n"
            "   - 단락 1: 사건/발언/연구의 가장 핵심적인 사실과 본질을 명확하고 완성도 높게 브리핑.\n"
            "   - 단락 2: 구체적 발생 배경, 관련 기업/인물, 수치 및 세부 진행 경과를 상세히 설명.\n"
            "   - 단락 3: 시장, 정책, 산업 생태계에 미칠 파급효과 및 주요 시사점을 전망.\n"
            "4. 중간에 문장이 끊기지 않도록 끝까지 완결된 문장으로 작성하라."
        )

        user_prompt = (
            f"[수집 분야: {category}]\n"
            f"{body_text}\n\n"
            "위 내용의 핵심 팩트와 수치가 누락되지 않도록 상세하고 깊이 있는 한국어 구어체 완결 문단 브리핑으로 작성해주세요. 인사말 없이 바로 브리핑을 시작하세요."
        )

        headers = {
            "Authorization": f"Bearer {curr_key}",
            "HTTP-Referer": "https://github.com/DONGJUN92/hyperalertfeedbot",
            "X-Title": "Alpha Intelligence Feed Bot",
            "Content-Type": "application/json",
        }

        active_model = self.selector.get_active_model()
        attempt_models = [active_model]
        with self.selector.lock:
            for c in self.selector.candidate_summary:
                cid = c.get("id")
                if cid and cid not in attempt_models:
                    attempt_models.append(cid)

        reliable_fallbacks = [
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "inclusionai/ling-3.0-flash-fin:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            DEFAULT_FALLBACK_MODEL,
        ]
        for fb in reliable_fallbacks:
            if fb not in attempt_models:
                attempt_models.append(fb)

        for mod in attempt_models[:5]:
            payload = {
                "model": mod,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 3000,
            }
            try:
                resp = requests.post(OPENROUTER_CHAT_URL, json=payload, headers=headers, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_text = choices[0].get("message", {}).get("content", "").strip()
                        cleaned_text = clean_ai_summary(raw_text)
                        if cleaned_text and len(cleaned_text) >= 20:
                            logger.info(f"[AISummarizer] Successfully generated briefing using {mod} ({len(cleaned_text)} chars)")
                            self.last_error = None
                            return cleaned_text
                elif resp.status_code == 401:
                    logger.warning(f"OpenRouter ({mod}) returned HTTP 401: Invalid API key.")
                    self.last_error = "OpenRouter HTTP 401: 인증 실패"
                    break
                else:
                    logger.warning(f"OpenRouter ({mod}) returned HTTP {resp.status_code}: {resp.text[:200]}")
                    self.last_error = f"OpenRouter HTTP {resp.status_code}"
            except Exception as e:
                logger.error(f"OpenRouter summarization exception on {mod}: {e}")
                self.last_error = f"네트워크 예외: {e}"

        return None


if __name__ == "__main__":
    summarizer = AISummarizer()
    print("Summarizer initialized. Initial active model:", summarizer.get_active_model())

