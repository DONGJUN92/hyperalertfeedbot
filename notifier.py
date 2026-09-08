import json
import logging
import time
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger("x_notifier")

# Standard bot headers to avoid Cloudflare/Telegram tarpit or timeout
BOT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}

# VIP Celebrity Metadata and Badges
CELEBRITY_BADGES = {
    "thsottiaux": ("🤖 AI 모델 & 인프라", "Tibo"),
    "sama": ("🧠 OpenAI & 프론티어 AI", "Sam Altman"),
    "elonmusk": ("⚡ Tesla · xAI · 빅테크 리더", "Elon Musk"),
    "realdonaldtrump": ("🏛️ 미국 정책 & 글로벌 거시", "Donald J. Trump"),
}


class Notifier:
    def __init__(self, bot_token: str, chat_id: str, ntfy_topic: str = "", summarizer=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.ntfy_topic = ntfy_topic.strip() if ntfy_topic else ""
        self.summarizer = summarizer

    def update_credentials(self, bot_token: str, chat_id: str, ntfy_topic: str = "", summarizer=None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.ntfy_topic = ntfy_topic.strip() if ntfy_topic else ""
        if summarizer is not None:
            self.summarizer = summarizer

    def send_telegram_message(self, text: str, disable_notification: bool = False, max_retries: int = 3) -> Optional[int]:
        """Send a message via Telegram bot with automatic retries. Returns message_id if successful."""
        if not self.bot_token or self.bot_token.startswith("YOUR_"):
            logger.warning("Telegram bot_token is not configured yet.")
            return None
        if not self.chat_id or self.chat_id.startswith("YOUR_"):
            logger.warning("Telegram chat_id is not configured yet.")
            return None

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": disable_notification,
        }

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, json=payload, headers=BOT_HEADERS, timeout=15)
                res_data = resp.json()
                if res_data.get("ok"):
                    return res_data["result"]["message_id"]
                else:
                    logger.error(f"Telegram API error (Attempt {attempt}): {res_data}")
            except Exception as e:
                logger.warning(f"Telegram send failed (Attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2.0 * attempt)
        return None

    def pin_telegram_message(self, message_id: int):
        """Pin a message to trigger high-priority push notification in Telegram."""
        if not self.bot_token or not self.chat_id or not message_id:
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/pinChatMessage"
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "disable_notification": False,
        }
        try:
            requests.post(url, json=payload, headers=BOT_HEADERS, timeout=10)
        except Exception as e:
            logger.error(f"Failed to pin Telegram message: {e}")

    def send_ntfy_push(self, title: str, message: str, click_url: str = "", is_emergency: bool = False):
        """
        Send push notification via free open-source ntfy.sh.
        If is_emergency is True, sends with 'urgent' priority (bypasses Do Not Disturb on Android).
        """
        if not self.ntfy_topic:
            return

        url = f"https://ntfy.sh/{self.ntfy_topic}"
        headers = {
            "Title": title.encode("utf-8").decode("latin-1", errors="ignore"),
            "Priority": "urgent" if is_emergency else "default",
            "Tags": "warning,loudspeaker" if is_emergency else "speech_balloon",
        }
        if click_url:
            headers["Click"] = click_url

        try:
            requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
            logger.info(f"ntfy notification sent to topic: {self.ntfy_topic}")
        except Exception as e:
            logger.error(f"Failed to send ntfy push: {e}")

    def notify_tweet(self, tweet: Dict[str, Any], matched_keywords: List[str]):
        """Format and dispatch notification for a new tweet using clean terminal typography."""
        username = tweet.get("username", "")
        clean_user = username.strip().lstrip("@").lower()
        badge_info = CELEBRITY_BADGES.get(clean_user, ("VIP", username))
        badge, display_name = badge_info

        author = tweet.get("author") or display_name
        time_str = tweet.get("time", "")
        text = tweet.get("text", "")
        url = tweet.get("url", "")

        is_emergency = len(matched_keywords) > 0

        safe_author = html_escape(author)
        safe_time = html_escape(time_str)
        safe_text = html_escape(text)

        header_tag = "[URGENT]" if is_emergency else f"[{badge}]"
        time_meta = f" · {safe_time}" if safe_time else ""

        if is_emergency:
            kw_str = ", ".join([f"<code>{html_escape(k)}</code>" for k in matched_keywords])
            msg = (
                f"<b>{header_tag} @{username}</b> ({safe_author}){time_meta}\n\n"
                f"<blockquote>{safe_text}</blockquote>\n\n"
                f"• <b>감지 키워드:</b> {kw_str}\n"
                f"• <b>원문 링크:</b> <a href=\"{url}\">x.com/{username}</a>"
            )
            msg_id = self.send_telegram_message(msg, disable_notification=False)
            if msg_id:
                self.pin_telegram_message(msg_id)

            time.sleep(0.5)
            self.send_telegram_message(
                f"⚡ <b>[긴급 경보]</b> @{username} 계정에서 긴급 키워드({kw_str})가 감지되었습니다.",
                disable_notification=False,
            )

            self.send_ntfy_push(
                title=f"[URGENT] @{username}: {', '.join(matched_keywords)}",
                message=text[:300],
                click_url=url,
                is_emergency=True,
            )
        else:
            msg = (
                f"<b>{header_tag} @{username}</b> ({safe_author}){time_meta}\n\n"
                f"<blockquote>{safe_text}</blockquote>\n\n"
                f"• <b>원문 링크:</b> <a href=\"{url}\">x.com/{username}</a>"
            )
            self.send_telegram_message(msg, disable_notification=False)

            if self.ntfy_topic:
                self.send_ntfy_push(
                    title=f"@{username}: {text[:100]}",
                    message=text[:300],
                    click_url=url,
                    is_emergency=False,
                )

    def notify_news(self, news_item: Dict[str, Any], matched_keywords: List[str]):
        """Format and dispatch notification for news with AI 3-bullet summary in blockquotes."""
        category = news_item.get("category", "정책 & 테크")
        source = news_item.get("source", "원문")
        title = news_item.get("title", "")
        summary = news_item.get("summary", "")
        url = news_item.get("url", "")
        published = news_item.get("published", "")

        is_emergency = len(matched_keywords) > 0

        # Determine clean category tag
        if "상법" in category or "상법" in title:
            tag = "POLICY/상법"
        elif "금융" in category or "경제" in category:
            tag = "MACRO POLICY"
        elif "논문" in category or "ArXiv" in source:
            tag = "AI RESEARCH"
        elif "GeekNews" in source or "긱뉴스" in category:
            tag = "GEEKNEWS"
        else:
            tag = "TECH"

        if is_emergency:
            tag = f"URGENT {tag}"

        safe_title = html_escape(title)
        safe_source = html_escape(source)
        safe_published = html_escape(published)

        # Generate AI Summary if summarizer is present
        ai_summary = None
        if self.summarizer:
            try:
                ai_summary = self.summarizer.summarize(title, summary, category)
            except Exception as e:
                logger.debug(f"AI summary error: {e}")

        # Clean blockquote body
        if ai_summary:
            body_block = html_escape(ai_summary)
        else:
            clean_s = (summary or "").strip()
            clean_t = (title or "").strip()
            if not clean_s or clean_s == clean_t or (len(clean_t) > 20 and clean_s.startswith(clean_t[:30])):
                body_block = "상세 속보 및 전체 분석 전문은 아래 1차 출처 링크에서 바로 확인할 수 있습니다."
            else:
                body_block = html_escape(clean_s)

        kw_line = ""
        if is_emergency:
            kw_str = ", ".join([f"<code>{html_escape(k)}</code>" for k in matched_keywords])
            kw_line = f"• <b>감지 키워드:</b> {kw_str}\n"

        pub_meta = f" · {safe_published}" if safe_published else ""

        msg = (
            f"<b>[{tag}] {safe_title}</b>\n"
            f"<code>{safe_source}</code>{pub_meta}\n\n"
            f"<blockquote>{body_block}</blockquote>\n\n"
            f"{kw_line}"
            f"• <b>원문 보기:</b> <a href=\"{url}\">1차 출처 바로가기</a>"
        )

        msg_id = self.send_telegram_message(msg, disable_notification=False)
        if is_emergency and msg_id:
            self.pin_telegram_message(msg_id)

        if is_emergency and self.ntfy_topic:
            self.send_ntfy_push(
                title=f"[{tag}] {title[:60]}",
                message=title,
                click_url=url,
                is_emergency=True,
            )


def html_escape(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

