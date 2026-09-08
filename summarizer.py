import logging
import os
import requests
from typing import Optional

logger = logging.getLogger("x_summarizer")


class AISummarizer:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    def update_api_key(self, api_key: str):
        self.api_key = api_key.strip()

    def summarize(self, title: str, content: str, category: str = "") -> Optional[str]:
        """
        Summarize news article or ArXiv paper into a concise 3-bullet insight using Gemini Flash.
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

        # Build prompt
        prompt = (
            "너는 글로벌 헤지펀드와 테크 창업자를 위한 최고급 인텔리전스 분석관이다.\n"
            f"분야: {category}\n"
            f"{body_text}\n\n"
            "위 내용을 바쁜 의사결정자가 3초 만에 핵심만 파악할 수 있도록 반드시 아래 형식의 3개 불릿으로 한국어로 요약하라.\n"
            "이모지는 일체 쓰지 말고, 군더더기 서론이나 결론 문장 없이 오직 3줄의 불릿(•)만 출력하라:\n"
            "• [핵심 결론] (가장 중요한 사실 1문장)\n"
            "• [세부 내용] (구체적인 수치, 대상, 배경 1문장)\n"
            "• [영향 및 시사점] (시장/산업/정책에 미칠 파급효과 1문장)\n"
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 300,
            }
        }
        headers = {"Content-Type": "application/json"}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        summary_text = parts[0].get("text", "").strip()
                        return summary_text
            else:
                logger.warning(f"Gemini API returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Gemini summarization failed: {e}")

        return None


if __name__ == "__main__":
    summarizer = AISummarizer()
    print("Summarizer initialized with key present:", bool(summarizer.api_key))
