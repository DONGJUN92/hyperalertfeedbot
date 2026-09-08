import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any, List

logger = logging.getLogger("x_notifier")

# VIP Celebrity Metadata and Badges
CELEBRITY_BADGES = {
    "thsottiaux": ("🤖 AI 모델 & 인프라", "Tibo"),
    "sama": ("🧠 OpenAI & 프론티어 AI", "Sam Altman"),
    "elonmusk": ("⚡ Tesla · xAI · 빅테크 리더", "Elon Musk"),
    "realdonaldtrump": ("🏛️ 미국 정책 & 글로벌 거시", "Donald J. Trump"),
}


class Notifier:
    def __init__(self, bot_token: str, chat_id: str, ntfy_topic: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.ntfy_topic = ntfy_topic.strip() if ntfy_topic else ""

    def update_credentials(self, bot_token: str, chat_id: str, ntfy_topic: str = ""):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.ntfy_topic = ntfy_topic.strip() if ntfy_topic else ""

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
        data = json.dumps(payload).encode("utf-8")

        for attempt in range(1, max_retries + 1):
            try:
                req = urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    if res_data.get("ok"):
                        return res_data["result"]["message_id"]
                    else:
                        logger.error(f"Telegram API error (Attempt {attempt}): {res_data}")
            except Exception as e:
                logger.warning(f"Telegram send failed (Attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(2.5 * attempt)  # Exponential backoff
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
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass
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
            "Tags": "warning,rotating_light,loudspeaker" if is_emergency else "speech_balloon",
        }
        if click_url:
            headers["Click"] = click_url

        data = message.encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"ntfy notification sent to topic: {self.ntfy_topic}")
        except Exception as e:
            logger.error(f"Failed to send ntfy push: {e}")

    def notify_tweet(self, tweet: Dict[str, Any], matched_keywords: List[str]):
        """Format and dispatch notification for a new tweet from VIP celebrities."""
        username = tweet.get("username", "")
        clean_user = username.strip().lstrip("@").lower()
        badge_info = CELEBRITY_BADGES.get(clean_user, ("🌟 글로벌 오피니언 리더", username))
        badge, display_name = badge_info

        author = tweet.get("author") or display_name
        text = tweet.get("text", "")
        url = tweet.get("url", "")

        is_emergency = len(matched_keywords) > 0

        safe_author = html_escape(author)
        safe_text = html_escape(text)

        if is_emergency:
            kw_str = ", ".join([f"<b>{html_escape(k)}</b>" for k in matched_keywords])
            msg = (
                f"🚨🚨🚨 <b>[긴급 경보: KEYWORD 감지]</b> 🚨🚨🚨\n\n"
                f"🏷️ <b>분류:</b> {badge}\n"
                f"👤 <b>작성자:</b> @{username} ({safe_author})\n"
                f"🎯 <b>감지 키워드:</b> {kw_str}\n\n"
                f"📝 <b>트윗 원문:</b>\n{safe_text}\n\n"
                f"🔗 <a href=\"{url}\">트윗 1차 원문 바로가기</a>\n"
                f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨"
            )
            msg_id = self.send_telegram_message(msg, disable_notification=False)
            if msg_id:
                self.pin_telegram_message(msg_id)

            time.sleep(0.5)
            self.send_telegram_message(
                f"⚡ <b>[긴급 알림 리마인더]</b> @{username} 님의 트윗에 긴급 키워드({kw_str})가 감지되었습니다!",
                disable_notification=False,
            )

            ntfy_title = f"🚨 긴급: @{username} [{', '.join(matched_keywords)}] 감지!"
            self.send_ntfy_push(
                title=ntfy_title,
                message=text[:500],
                click_url=url,
                is_emergency=True,
            )
        else:
            msg = (
                f"📢 <b>[VIP 트윗]</b> {badge}\n"
                f"👤 <b>@{username}</b> ({safe_author})\n\n"
                f"{safe_text}\n\n"
                f"🔗 <a href=\"{url}\">트윗 1차 원문 바로가기</a>"
            )
            self.send_telegram_message(msg, disable_notification=False)

            if self.ntfy_topic:
                self.send_ntfy_push(
                    title=f"새 트윗: @{username} ({safe_author})",
                    message=text[:500],
                    click_url=url,
                    is_emergency=False,
                )

    def notify_news(self, news_item: Dict[str, Any], matched_keywords: List[str]):
        """Format and dispatch notification for 1st-source news (AI papers, economic/commercial law policy)."""
        category = news_item.get("category", "📰 정책 & 테크")
        source = news_item.get("source", "원문 소스")
        title = news_item.get("title", "")
        summary = news_item.get("summary", "")
        url = news_item.get("url", "")
        published = news_item.get("published", "")

        is_emergency = len(matched_keywords) > 0

        safe_title = html_escape(title)
        safe_summary = html_escape(summary)
        safe_source = html_escape(source)

        if is_emergency:
            kw_str = ", ".join([f"<b>{html_escape(k)}</b>" for k in matched_keywords])
            msg = (
                f"🚨🚨🚨 <b>[정책/테크 긴급 속보]</b> 🚨🚨🚨\n\n"
                f"🏷️ <b>분야:</b> {category} ({safe_source})\n"
                f"🎯 <b>감지 키워드:</b> {kw_str}\n"
                f"📌 <b>제목:</b> {safe_title}\n\n"
                f"📝 <b>원문 요약 / 초록:</b>\n{safe_summary}\n\n"
                f"⏱️ <b>일시:</b> {published}\n"
                f"🔗 <a href=\"{url}\">1차 원문 / 공문 바로가기</a>\n"
                f"🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨"
            )
            msg_id = self.send_telegram_message(msg, disable_notification=False)
            if msg_id:
                self.pin_telegram_message(msg_id)

            self.send_ntfy_push(
                title=f"🚨 긴급 정책/테크: [{', '.join(matched_keywords)}]",
                message=title,
                click_url=url,
                is_emergency=True,
            )
        else:
            msg = (
                f"📰 <b>[{category}]</b> {safe_source}\n\n"
                f"📌 <b>{safe_title}</b>\n\n"
                f"{safe_summary}\n\n"
                f"⏱️ {published}\n"
                f"🔗 <a href=\"{url}\">1차 원문 바로가기</a>"
            )
            self.send_telegram_message(msg, disable_notification=False)


def html_escape(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
