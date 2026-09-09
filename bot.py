import http.server
import json
import logging
import os
import re
import socketserver
import sys
import threading
import time
import urllib.parse
import requests
from typing import Dict, Any, List, Optional

from scraper import fetch_latest_tweets
from notifier import Notifier, CELEBRITY_BADGES
from summarizer import AISummarizer, get_env_api_key, validate_api_key
from news_collector import (
    fetch_all_curated_news,
    fetch_korea_policy_news,
    fetch_geeknews,
    fetch_arxiv_ai_papers,
    fetch_techcrunch_ai_news,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("x_bot")


def extract_x_username(input_str: str) -> str:
    """Extract clean X/Twitter handle from URL, @mention, or raw username."""
    if not input_str:
        return ""
    s = input_str.strip()
    url_pattern = r"(?:https?:\/\/)?(?:www\.|mobile\.)?(?:x\.com|twitter\.com)\/([A-Za-z0-9_]{1,30})"
    match = re.search(url_pattern, s, re.IGNORECASE)
    if match:
        user = match.group(1).lower()
        if user in ("home", "explore", "notifications", "messages", "i", "search", "settings", "help"):
            return ""
        return user
    clean = s.lstrip("@").strip()
    if re.match(r"^[A-Za-z0-9_]{1,30}$", clean):
        return clean.lower()
    return ""

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
START_TIME = time.time()


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler to satisfy Koyeb Web Service health checks."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "status": "healthy",
            "service": "alpha-intelligence-telegram-bot",
            "uptime_seconds": int(time.time() - START_TIME),
        }
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format, *args):
        return


def run_health_server(port: int):
    try:
        with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
            logger.info(f"Health check HTTP server listening on port {port}...")
            httpd.serve_forever()
    except Exception as e:
        logger.error(f"Health check server error on port {port}: {e}")


def run_keep_alive_ping():
    """Prevent Render free tier spin-down by pinging external URL every 10 minutes."""
    while True:
        time.sleep(600)  # Every 10 minutes
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            try:
                requests.get(render_url, headers={"User-Agent": "RenderKeepAlive/1.0"}, timeout=10)
                logger.info(f"Keep-alive self-ping sent to {render_url}")
            except Exception as e:
                logger.debug(f"Keep-alive ping error: {e}")



class ConfigManager:
    def __init__(self, filepath: str = CONFIG_PATH):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.config = self.load()
        self._apply_env_overrides()

    def load(self) -> Dict[str, Any]:
        with self.lock:
            if not os.path.exists(self.filepath):
                default_config = {
                    "bot_token": "YOUR_TELEGRAM_BOT_TOKEN_HERE",
                    "chat_id": "YOUR_TELEGRAM_CHAT_ID_HERE",
                    "monitored_users": ["thsottiaux", "sama", "elonmusk", "realDonaldTrump"],
                    "priority_keywords": ["reset", "상법", "자본시장", "반독점", "tariff", "sec", "openai", "gpt"],
                    "check_interval_seconds": 30,
                    "enable_news_feed": True,
                    "news_check_interval_seconds": 300,
                    "ntfy_topic": "",
                    "openrouter_api_key": "",
                    "seen_tweet_ids": [],
                    "seen_news_ids": [],
                    "seen_news_titles": [],
                }
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, indent=2, ensure_ascii=False)
                return default_config

            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")
                return {}

    def _apply_env_overrides(self):
        env_bot_token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if env_bot_token:
            self.config["bot_token"] = env_bot_token.strip()

        env_chat_id = os.environ.get("CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if env_chat_id:
            self.config["chat_id"] = env_chat_id.strip()

        env_users = os.environ.get("MONITORED_USERS")
        if env_users:
            users = [extract_x_username(u) for u in env_users.split(",") if extract_x_username(u)]
            if users:
                self.config["monitored_users"] = users

        env_keywords = os.environ.get("PRIORITY_KEYWORDS")
        if env_keywords:
            kws = [k.strip() for k in env_keywords.split(",") if k.strip()]
            if kws:
                self.config["priority_keywords"] = kws

        env_interval = os.environ.get("CHECK_INTERVAL")
        if env_interval:
            try:
                self.config["check_interval_seconds"] = max(10, int(env_interval))
            except ValueError:
                pass

        env_news = os.environ.get("ENABLE_NEWS_FEED")
        if env_news:
            self.config["enable_news_feed"] = env_news.lower() in ["true", "1", "yes"]

        env_ntfy = os.environ.get("NTFY_TOPIC")
        if env_ntfy is not None:
            self.config["ntfy_topic"] = env_ntfy.strip()

        env_or = get_env_api_key()
        if env_or:
            self.config["openrouter_api_key"] = env_or.strip()

    def save(self):
        with self.lock:
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to write config file: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        self.config[key] = value
        self.save()

    def add_user(self, input_str: str) -> bool:
        username = extract_x_username(input_str)
        if not username:
            return False
        users = [u.lower() for u in self.config.get("monitored_users", [])]
        if username not in users:
            self.config.setdefault("monitored_users", []).append(username)
            self.save()
            return True
        return False

    def remove_user(self, input_str: str) -> bool:
        username = extract_x_username(input_str)
        if not username:
            return False
        users = self.config.get("monitored_users", [])
        original_len = len(users)
        self.config["monitored_users"] = [u for u in users if u.lower() != username]
        if len(self.config["monitored_users"]) < original_len:
            self.save()
            return True
        return False

    def add_keyword(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        keywords = [k.lower() for k in self.config.get("priority_keywords", [])]
        if keyword and keyword not in keywords:
            self.config.setdefault("priority_keywords", []).append(keyword)
            self.save()
            return True
        return False

    def remove_keyword(self, keyword: str) -> bool:
        keyword = keyword.strip().lower()
        keywords = self.config.get("priority_keywords", [])
        original_len = len(keywords)
        self.config["priority_keywords"] = [k for k in keywords if k.lower() != keyword]
        if len(self.config["priority_keywords"]) < original_len:
            self.save()
            return True
        return False

    def is_seen(self, tweet_id: str) -> bool:
        return tweet_id in self.config.get("seen_tweet_ids", [])

    def mark_seen(self, tweet_id: str):
        seen = self.config.setdefault("seen_tweet_ids", [])
        if tweet_id not in seen:
            seen.append(tweet_id)
            if len(seen) > 1000:
                self.config["seen_tweet_ids"] = seen[-1000:]
            self.save()

    def is_title_seen(self, title: str, threshold: float = 0.45) -> bool:
        if not title:
            return False
        clean = re.sub(r"[^a-zA-Z0-9가-힣\s]", " ", title)
        words = set([w.lower() for w in clean.split() if len(w) >= 2])
        if not words:
            return False
        recent_titles = self.config.get("seen_news_titles", [])
        for past in recent_titles[-200:]:
            clean_p = re.sub(r"[^a-zA-Z0-9가-힣\s]", " ", past)
            words_p = set([w.lower() for w in clean_p.split() if len(w) >= 2])
            if not words_p:
                continue
            inter = len(words.intersection(words_p))
            union = len(words.union(words_p))
            if union > 0 and (inter / union) >= threshold:
                return True
        return False

    def is_news_seen(self, news_id: str, title: str = "") -> bool:
        if news_id in self.config.get("seen_news_ids", []):
            return True
        if title and self.is_title_seen(title):
            return True
        return False

    def mark_news_seen(self, news_id: str, title: str = ""):
        seen = self.config.setdefault("seen_news_ids", [])
        if news_id not in seen:
            seen.append(news_id)
            if len(seen) > 1000:
                self.config["seen_news_ids"] = seen[-1000:]
        if title:
            titles = self.config.setdefault("seen_news_titles", [])
            if title not in titles:
                titles.append(title)
                if len(titles) > 1000:
                    self.config["seen_news_titles"] = titles[-1000:]
        self.save()


class TwitterTelegramBot:
    def __init__(self):
        self.config_mgr = ConfigManager()
        self.summarizer = AISummarizer(api_key=self.config_mgr.get("openrouter_api_key", ""))
        self.notifier = Notifier(
            bot_token=self.config_mgr.get("bot_token", ""),
            chat_id=self.config_mgr.get("chat_id", ""),
            ntfy_topic=self.config_mgr.get("ntfy_topic", ""),
            summarizer=self.summarizer,
        )
        self.running = True
        self.last_update_id = 0

    def start(self):
        logger.info("Starting Alpha Intelligence Feed Bot...")

        port = int(os.environ.get("PORT", 8000))
        health_thread = threading.Thread(target=run_health_server, args=(port,), daemon=True)
        health_thread.start()

        keep_alive_thread = threading.Thread(target=run_keep_alive_ping, daemon=True)
        keep_alive_thread.start()

        bot_token = self.config_mgr.get("bot_token", "")
        if not bot_token or bot_token.startswith("YOUR_"):
            logger.warning("=" * 60)
            logger.warning("주의: 'BOT_TOKEN'이 설정되지 않았습니다.")
            logger.warning("Koyeb 환경변수 또는 config.json에 텔레그램 bot_token을 등록해주세요.")
            logger.warning("=" * 60)

        # 1. Start background tweet monitor thread
        monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        monitor_thread.start()

        # 2. Start background 1st-source news monitor thread
        news_thread = threading.Thread(target=self.news_monitor_loop, daemon=True)
        news_thread.start()

        # 3. Start Telegram command listener in main thread
        self.telegram_command_loop()

    def monitor_loop(self):
        """Periodically scrapes VIP celebrity profiles with anti-blocking delays."""
        logger.info("Initializing baseline tweets for VIP celebrities...")
        users = self.config_mgr.get("monitored_users", [])
        seen_ids = set(self.config_mgr.get("seen_tweet_ids", []))

        if not seen_ids:
            for u in users:
                logger.info(f"Fetching baseline for @{u}...")
                tweets = fetch_latest_tweets(u)
                for t in tweets:
                    self.config_mgr.mark_seen(t["id"])
                time.sleep(1.5)  # Anti-blocking pacing
            logger.info("VIP Tweet baseline registered. Live tweets will trigger alerts.")

        while self.running:
            try:
                users = list(self.config_mgr.get("monitored_users", []))
                keywords = [k.lower() for k in self.config_mgr.get("priority_keywords", [])]

                for username in users:
                    tweets = fetch_latest_tweets(username)
                    for t in reversed(tweets):
                        tweet_id = t["id"]
                        if not self.config_mgr.is_seen(tweet_id):
                            tweet_content = (t.get("text", "") + " " + t.get("author", "")).lower()
                            matched = [k for k in keywords if k in tweet_content]

                            logger.info(
                                f"New tweet detected from @{username} (ID: {tweet_id}). "
                                f"Priority keywords matched: {matched}"
                            )

                            self.notifier.notify_tweet(t, matched)
                            self.config_mgr.mark_seen(tweet_id)

                    time.sleep(1.5)  # 1.5s delay between different VIP user scrapes

            except Exception as e:
                logger.error(f"Error in tweet monitor loop: {e}", exc_info=True)

            interval = max(10, int(self.config_mgr.get("check_interval_seconds", 30)))
            time.sleep(interval)

    def news_monitor_loop(self):
        """Periodically collects AI papers and economic policy / commercial law news."""
        logger.info("Initializing baseline for economic policy & AI tech news...")
        # Register any current news items as initial baseline so old posts are never blasted on startup
        initial_news = fetch_all_curated_news()
        new_baseline_count = 0
        for n in initial_news:
            nid = n["id"]
            ntitle = n.get("title", "")
            if not self.config_mgr.is_news_seen(nid, ntitle):
                self.config_mgr.mark_news_seen(nid, ntitle)
                new_baseline_count += 1
        if new_baseline_count > 0:
            logger.info(f"Registered {new_baseline_count} news items as initial baseline.")

        while self.running:
            try:
                if self.config_mgr.get("enable_news_feed", True):
                    news_items = fetch_all_curated_news()
                    keywords = [k.lower() for k in self.config_mgr.get("priority_keywords", [])]

                    for item in news_items:
                        nid = item["id"]
                        title = item.get("title", "")
                        if not self.config_mgr.is_news_seen(nid, title):
                            search_text = (title + " " + item.get("summary", "")).lower()
                            matched = [k for k in keywords if k in search_text]

                            logger.info(f"New news item: [{item['category']}] {title[:40]}... Matched: {matched}")
                            self.notifier.notify_news(item, matched)
                            self.config_mgr.mark_news_seen(nid, title)

            except Exception as e:
                logger.error(f"Error in news monitor loop: {e}", exc_info=True)

            news_interval = max(60, int(self.config_mgr.get("news_check_interval_seconds", 300)))
            time.sleep(news_interval)

    def run_live_test_thread(self):
        """Fetch and deliver the single most recent item from every live source."""
        try:
            curr_key = self.summarizer.api_key
            has_ai = bool(curr_key and not curr_key.startswith("YOUR_"))
            active_m = self.summarizer.get_active_model()
            ai_badge = f"가동 중 🟢 (모델: <code>{active_m}</code>)" if has_ai else "미설정 ⚪ (원문 발췌 모드)"

            self.notifier.send_telegram_message(
                "🔍 <b>[라이브 테스트 가동]</b>\n\n"
                f"• <b>AI 구어체 브리핑:</b> {ai_badge}\n"
                "• <b>수집 채널:</b> VIP 트윗 + 정책·상법 + 긱뉴스 + AI 논문 + 테크 속보\n\n"
                "가장 최근 원문 1건씩을 실시간으로 가져옵니다...\n"
                "<i>(AI 브리핑 생성으로 약 15~20초 소요됩니다)</i>"
            )

            # 1. VIP Twitter Accounts
            users = self.config_mgr.get("monitored_users", ["thsottiaux", "sama", "elonmusk", "realDonaldTrump"])
            keywords = [k.lower() for k in self.config_mgr.get("priority_keywords", [])]

            for u in users:
                tweets = fetch_latest_tweets(u)
                if tweets:
                    top_t = tweets[0]
                    tweet_content = (top_t.get("text", "") + " " + top_t.get("author", "")).lower()
                    matched = [k for k in keywords if k in tweet_content]
                    self.notifier.notify_tweet(top_t, matched)
                time.sleep(2.0)  # Safe delay to prevent Telegram 429 rate limit

            # 2. Economic Policy & Commercial Act (상법)
            policy_items = fetch_korea_policy_news(["상법 개정"], limit_per_query=1)
            if policy_items:
                item = policy_items[0]
                text_to_check = (item.get("title", "") + " " + item.get("summary", "")).lower()
                matched = [k for k in keywords if k in text_to_check]
                self.notifier.notify_news(item, matched)
            time.sleep(2.0)

            # 3. GeekNews (https://news.hada.io/) - notify without keywords
            geek_items = fetch_geeknews(limit=1)
            if geek_items:
                item = geek_items[0]
                text_to_check = (item.get("title", "") + " " + item.get("summary", "")).lower()
                matched = [k for k in keywords if k in text_to_check]
                self.notifier.notify_news(item, matched)
            time.sleep(2.0)

            # 4. ArXiv CS.AI Frontier Papers
            arxiv_items = fetch_arxiv_ai_papers(limit=1)
            if arxiv_items:
                self.notifier.notify_news(arxiv_items[0], matched_keywords=[])
            time.sleep(2.0)

            # 5. TechCrunch AI Industry News
            tc_items = fetch_techcrunch_ai_news(limit=1)
            if tc_items:
                self.notifier.notify_news(tc_items[0], matched_keywords=[])
            time.sleep(2.0)

            self.notifier.send_telegram_message(
                "✅ <b>[전체 소스 실시간 수신 검증 완료]</b>\n"
                "모든 채널의 최신 데이터가 정상적으로 수신 및 발송되었습니다!"
            )
        except Exception as e:
            logger.error(f"Error during live test: {e}", exc_info=True)
            self.notifier.send_telegram_message(f"⚠️ 라이브 테스트 도중 오류 발생: {e}")

    def telegram_command_loop(self):
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        while self.running:
            bot_token = self.config_mgr.get("bot_token", "")
            if not bot_token or bot_token.startswith("YOUR_"):
                time.sleep(5)
                continue

            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            params = {"offset": self.last_update_id + 1, "timeout": 20}
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=30)
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        self.last_update_id = max(self.last_update_id, update["update_id"])
                        if "message" in update and "text" in update["message"]:
                            self.handle_command(update["message"])
            except Exception as e:
                logger.debug(f"getUpdates error (polling): {e}")
                time.sleep(3)

    def handle_command(self, msg: Dict[str, Any]):
        chat_id = str(msg["chat"]["id"])
        text = msg.get("text", "").strip()
        configured_chat_id = str(self.config_mgr.get("chat_id", ""))

        if not configured_chat_id or configured_chat_id.startswith("YOUR_"):
            self.config_mgr.set("chat_id", chat_id)
            self.notifier.update_credentials(
                self.config_mgr.get("bot_token"),
                chat_id,
                self.config_mgr.get("ntfy_topic", ""),
            )
            self.notifier.send_telegram_message(
                f"🎉 <b>환영합니다! (Alpha Intelligence Terminal)</b>\n"
                f"이 채팅방(Chat ID: <code>{chat_id}</code>)이 VIP 알림 수신처로 자동 등록되었습니다.\n"
                f"/help 를 입력하여 사용 가능한 명령어를 확인해보세요."
            )
            return

        if chat_id != configured_chat_id:
            logger.warning(f"Unauthorized command from chat_id: {chat_id}")
            return

        # Convenience: Allow user to paste OpenRouter API key (sk-or-) directly
        clean_text = text.strip()
        if clean_text.startswith("sk-or-") and len(clean_text) >= 20 and " " not in clean_text:
            self.config_mgr.set("openrouter_api_key", clean_text)
            self.summarizer.update_api_key(clean_text)
            
            is_valid, val_msg = validate_api_key(clean_text)
            if is_valid:
                self.notifier.send_telegram_message(
                    f"✅ <b>OpenRouter API 키 등록 및 인증 성공! 🟢</b>\n\n"
                    f"• 상태: <b>{val_msg}</b>\n"
                    f"• 활성 모델: <code>{self.summarizer.get_active_model()}</code>\n\n"
                    "지금부터 모든 VIP 트윗과 뉴스에 한국어 구어체 브리핑이 실시간 생성됩니다.\n"
                    "<code>/models</code> 명령어로 현재 모델 상태를 확인할 수 있습니다."
                )
            else:
                self.notifier.send_telegram_message(
                    f"⚠️ <b>OpenRouter API 키 인증 오류 🔴</b>\n\n"
                    f"• 오류 원인: <code>{val_msg}</code>\n\n"
                    "입력하신 키가 OpenRouter에서 거부되었습니다. 올바른 키인지 다시 확인해주세요."
                )
            return

        # Convenience: Allow user to paste X / Twitter profile or post link directly into chat
        if not clean_text.startswith("/") and ("x.com" in clean_text.lower() or "twitter.com" in clean_text.lower()):
            extracted = extract_x_username(clean_text)
            if extracted:
                current_users = [u.lower() for u in self.config_mgr.get("monitored_users", [])]
                if extracted in current_users:
                    self.notifier.send_telegram_message(
                        f"ℹ️ <b>이미 모니터링 중인 X 링크입니다.</b>\n\n"
                        f"• 계정: <b>@{extracted}</b>\n"
                        f"• 링크: https://x.com/{extracted}\n\n"
                        f"💡 모니터링 해제: <code>/del_link {extracted}</code>"
                    )
                else:
                    self.config_mgr.add_user(extracted)
                    badge_info = CELEBRITY_BADGES.get(extracted)
                    desc_str = f" ({badge_info[0]} - {badge_info[1]})" if badge_info else ""
                    self.notifier.send_telegram_message(
                        f"🔗 <b>X(트위터) 링크 감지! 모니터링 대상에 추가되었습니다.</b>\n\n"
                        f"• 계정: <b>@{extracted}</b>{desc_str}\n"
                        f"• 프로필: https://x.com/{extracted}\n\n"
                        f"지금부터 새 트윗이 게시되면 실시간 AI 브리핑을 보내드립니다.\n"
                        f"• 전체 링크 목록: <code>/links</code>\n"
                        f"• 링크 삭제: <code>/del_link {extracted}</code>"
                    )
                return

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ["/start", "/help"]:
            reply = (
                "🏛️ <b>Alpha Intelligence Feed 터미널 안내</b>\n\n"
                "📋 <b>상태 및 점검</b>\n"
                "• <code>/list</code> : 감시 계정, 키워드, AI 요약 설정 조회\n"
                "• <code>/links</code> : 현재 연결된 X(트위터) 모니터링 링크 전체 조회\n"
                "• <code>/celebs</code> : 등록된 VIP 오피니언 리더 확인\n"
                "• <code>/status</code> : 봇 작동 상태 및 Uptime 확인\n"
                "• <code>/test</code> 또는 <code>/check</code> : 모든 소스에서 최신 원문 1건씩 즉시 실시간 수신 점검\n\n"
                "🔗 <b>X(트위터) 연결 링크 관리</b>\n"
                "• <code>/add_link &lt;URL|아이디&gt;</code> : X 링크/계정 추가 (채팅창에 URL 바로 전송해도 자동 등록!)\n"
                "• <code>/del_link &lt;URL|아이디&gt;</code> : X 링크/계정 모니터링 해제\n"
                "• <code>/add_user</code>, <code>/del_user</code> : 기존 아이디 전용 명령어도 계속 지원\n\n"
                "🤖 <b>OpenRouter 무료 모델 자동 AI 구어체 브리핑</b>\n"
                "• <code>/openrouter &lt;API_KEY&gt;</code> : OpenRouter API 키 등록 (키만 바로 전송해도 자동 인식)\n"
                "• <code>/models</code> : 현재 선정된 활성 모델 및 1시간 주기 평가 현황 확인\n"
                "• <code>/eval_models</code> : 지금 즉시 무료 모델 핑 테스트 및 최적 모델 재평가\n"
                "• <code>/openrouter clear</code> : 키 삭제 (기본 발췌 모드로 복귀)\n\n"
                "🎯 <b>키워드 관리 (긴급 핀/사이렌 알림)</b>\n"
                "• <code>/add_keyword &lt;단어&gt;</code> : 긴급 키워드 추가 (예: <code>/add_keyword 상법</code>)\n"
                "• <code>/del_keyword &lt;단어&gt;</code> : 긴급 키워드 제거\n\n"
                "📰 <b>1차 소스 뉴스 피드</b>\n"
                "• <code>/news_toggle</code> : AI 논문 및 경제 정책 뉴스 피드 ON/OFF\n\n"
                "⚙️ <b>추가 설정</b>\n"
                "• <code>/interval &lt;초&gt;</code> : 감시 주기 변경\n"
                "• <code>/set_ntfy &lt;토픽&gt;</code> : ntfy 방해금지 무시 사이렌 연동"
            )
            self.notifier.send_telegram_message(reply)

        elif cmd == "/celebs":
            reply = (
                "🌟 <b>현재 등록된 VIP 글로벌 오피니언 리더</b>\n\n"
                "• 🤖 <b>@thsottiaux</b> : Tibo (Astra & AI 인프라 리더)\n"
                "  🔗 https://x.com/thsottiaux\n"
                "• 🧠 <b>@sama</b> : Sam Altman (OpenAI CEO)\n"
                "  🔗 https://x.com/sama\n"
                "• ⚡ <b>@elonmusk</b> : Elon Musk (Tesla, xAI, X)\n"
                "  🔗 https://x.com/elonmusk\n"
                "• 🏛️ <b>@realDonaldTrump</b> : Donald J. Trump (미국 정책 & 글로벌 거시)\n"
                "  🔗 https://x.com/realDonaldTrump\n\n"
                "추가 링크 등록: <code>/add_link &lt;URL|아이디&gt;</code> (또는 프로필 URL 바로 전송)"
            )
            self.notifier.send_telegram_message(reply)

        elif cmd == "/news_toggle":
            curr = self.config_mgr.get("enable_news_feed", True)
            new_val = not curr
            self.config_mgr.set("enable_news_feed", new_val)
            state_str = "활성화(ON) 🟢" if new_val else "비활성화(OFF) 🔴"
            self.notifier.send_telegram_message(f"📰 AI 및 경제 정책 1차 뉴스 피드가 <b>{state_str}</b> 되었습니다.")

        elif cmd == "/list":
            users = self.config_mgr.get("monitored_users", [])
            keywords = self.config_mgr.get("priority_keywords", [])
            interval = self.config_mgr.get("check_interval_seconds", 30)
            news_feed = "ON 🟢" if self.config_mgr.get("enable_news_feed", True) else "OFF 🔴"
            ntfy = self.config_mgr.get("ntfy_topic", "설정 안 됨")
            or_key = self.config_mgr.get("openrouter_api_key", "")
            has_ai = bool(or_key and not or_key.startswith("YOUR_")) or bool(self.summarizer.api_key)
            status_info = self.summarizer.get_status()
            active_model = self.summarizer.get_active_model()
            latency = status_info.get("latency_seconds", 0.0)
            is_valid, val_msg = self.summarizer.validate_current_key()
            if has_ai:
                ai_display = f"OpenRouter [{active_model}] 🟢" if is_valid else f"인증 실패 🔴 ({val_msg})"
            else:
                ai_display = "미연동 (기본 발췌) ⚪"

            u_list = "\n".join([f"  • @{u} (https://x.com/{u})" for u in users]) if users else "  (없음)"
            k_list = "\n".join([f"  • <b>{k}</b>" for k in keywords]) if keywords else "  (없음)"

            reply = (
                "⚙️ <b>Alpha Intelligence 모니터링 설정 현황</b>\n\n"
                f"👤 <b>감시 중인 VIP 계정 ({len(users)}개):</b>\n{u_list}\n\n"
                f"🚨 <b>긴급 경보 키워드 ({len(keywords)}개):</b>\n{k_list}\n\n"
                f"📰 <b>1차 소스 뉴스 피드:</b> {news_feed}\n"
                f"🤖 <b>AI 구어체 브리핑 엔진:</b> {ai_display}\n"
                f"⏱️ <b>트위터 확인 주기:</b> {interval}초\n"
                f"🔔 <b>ntfy 사이렌 토픽:</b> <code>{ntfy}</code>"
            )
            self.notifier.send_telegram_message(reply)

        elif cmd in ["/links", "/x_links", "/x_list"]:
            users = self.config_mgr.get("monitored_users", [])
            if not users:
                self.notifier.send_telegram_message(
                    "ℹ️ 현재 등록된 X(트위터) 모니터링 링크가 없습니다.\n\n"
                    "<code>/add_link &lt;URL&gt;</code> 명령어로 프로필 링크를 추가해보세요!"
                )
            else:
                lines = [
                    f"🔗 <b>연결된 X(트위터) 모니터링 링크 목록 ({len(users)}개)</b>\n",
                    "새 트윗이 감지되면 즉시 수집하여 AI 브리핑을 전달합니다.\n",
                ]
                for idx, u in enumerate(users, 1):
                    u_lower = u.lower()
                    badge_info = CELEBRITY_BADGES.get(u_lower)
                    if badge_info:
                        icon_topic, name = badge_info
                        lines.append(f"{idx}. {icon_topic} <b>{name}</b> (<code>@{u}</code>)")
                    else:
                        lines.append(f"{idx}. 👤 <b>@{u}</b>")
                    lines.append(f"   🔗 https://x.com/{u}\n")

                lines.append(
                    "💡 <b>링크 관리 명령어:</b>\n"
                    "• 링크 추가: <code>/add_link &lt;URL|아이디&gt;</code> (채팅창에 URL 바로 전송 가능)\n"
                    "• 링크 삭제: <code>/del_link &lt;URL|아이디&gt;</code>"
                )
                self.notifier.send_telegram_message("\n".join(lines))

        elif cmd in ["/add_link", "/add_x", "/add_user"]:
            if not arg:
                self.notifier.send_telegram_message(
                    "❌ 추가할 X(트위터) 링크 또는 계정 아이디를 입력해주세요.\n\n"
                    "• URL 예시: <code>/add_link https://x.com/vitalikbuterin</code>\n"
                    "• 아이디 예시: <code>/add_link vitalikbuterin</code>\n"
                    "*(채팅창에 프로필 URL을 바로 붙여넣어도 자동 등록됩니다)*"
                )
                return

            target_user = extract_x_username(arg)
            if not target_user:
                self.notifier.send_telegram_message(
                    "❌ 유효한 X(트위터) 링크 또는 아이디 형식이 아닙니다.\n"
                    "예: <code>https://x.com/sama</code> 또는 <code>sama</code>"
                )
                return

            current_users = [u.lower() for u in self.config_mgr.get("monitored_users", [])]
            if target_user in current_users:
                self.notifier.send_telegram_message(
                    f"ℹ️ 이미 모니터링 중인 X 링크/계정입니다.\n\n"
                    f"• 계정: <b>@{target_user}</b>\n"
                    f"• 링크: https://x.com/{target_user}"
                )
                return

            if self.config_mgr.add_user(target_user):
                badge_info = CELEBRITY_BADGES.get(target_user)
                desc_str = f" ({badge_info[0]} - {badge_info[1]})" if badge_info else ""
                self.notifier.send_telegram_message(
                    f"✅ <b>X(트위터) 모니터링 링크 추가 완료!</b>\n\n"
                    f"• 계정: <b>@{target_user}</b>{desc_str}\n"
                    f"• 링크: https://x.com/{target_user}\n\n"
                    f"새 트윗이 올라오면 실시간 AI 브리핑과 함께 전송됩니다.\n"
                    f"(목록: <code>/links</code>, 삭제: <code>/del_link {target_user}</code>)"
                )
            else:
                self.notifier.send_telegram_message("❌ 계정 추가 중 오류가 발생했습니다.")

        elif cmd in ["/del_link", "/del_x", "/del_user", "/remove_link"]:
            if not arg:
                self.notifier.send_telegram_message(
                    "❌ 제거할 X(트위터) 링크 또는 계정 아이디를 입력해주세요.\n\n"
                    "• URL 예시: <code>/del_link https://x.com/vitalikbuterin</code>\n"
                    "• 아이디 예시: <code>/del_link vitalikbuterin</code>"
                )
                return

            target_user = extract_x_username(arg)
            if not target_user:
                self.notifier.send_telegram_message(
                    "❌ 유효한 X(트위터) 링크 또는 아이디 형식이 아닙니다.\n"
                    "예: <code>https://x.com/sama</code> 또는 <code>sama</code>"
                )
                return

            if self.config_mgr.remove_user(target_user):
                self.notifier.send_telegram_message(
                    f"🗑️ <b>X(트위터) 모니터링 링크 제거 완료</b>\n\n"
                    f"• 계정: <b>@{target_user}</b>\n"
                    f"• 링크: https://x.com/{target_user}\n\n"
                    f"모니터링 대상에서 제외되었습니다. (목록 확인: <code>/links</code>)"
                )
            else:
                self.notifier.send_telegram_message(
                    f"ℹ️ 모니터링 목록에 등록되어 있지 않은 계정입니다: <b>@{target_user}</b>\n"
                    f"(현재 목록 확인: <code>/links</code>)"
                )

        elif cmd == "/add_keyword":
            if not arg:
                self.notifier.send_telegram_message("❌ 추가할 키워드를 입력해주세요.\n예: <code>/add_keyword 상법</code>")
                return
            if self.config_mgr.add_keyword(arg):
                self.notifier.send_telegram_message(f"🚨 긴급 키워드 <b>'{arg}'</b> 가 추가되었습니다!\n이 단어가 포함된 트윗/뉴스는 핀 고정 및 사이렌으로 알립니다.")
            else:
                self.notifier.send_telegram_message("ℹ️ 이미 등록되어 있는 키워드입니다.")

        elif cmd == "/del_keyword":
            if not arg:
                self.notifier.send_telegram_message("❌ 제거할 키워드를 입력해주세요.\n예: <code>/del_keyword 상법</code>")
                return
            if self.config_mgr.remove_keyword(arg):
                self.notifier.send_telegram_message(f"🗑️ 긴급 키워드 <b>'{arg}'</b> 가 목록에서 제거되었습니다.")
            else:
                self.notifier.send_telegram_message("ℹ️ 해당 키워드가 목록에 없습니다.")

        elif cmd == "/interval":
            try:
                sec = int(arg)
                if sec < 10:
                    self.notifier.send_telegram_message("⚠️ 최소 주기는 10초 이상으로 설정해주세요.")
                    return
                self.config_mgr.set("check_interval_seconds", sec)
                self.notifier.send_telegram_message(f"⏱️ 모니터링 주기가 <b>{sec}초</b>로 변경되었습니다.")
            except ValueError:
                self.notifier.send_telegram_message("❌ 올바른 숫자를 입력해주세요.\n예: <code>/interval 30</code>")

        elif cmd == "/set_ntfy":
            if arg.lower() in ["clear", "none", "삭제"]:
                self.config_mgr.set("ntfy_topic", "")
                self.notifier.update_credentials(
                    self.config_mgr.get("bot_token"),
                    self.config_mgr.get("chat_id"),
                    "",
                )
                self.notifier.send_telegram_message("🔔 ntfy 토픽 연동이 해제되었습니다.")
            else:
                topic = arg.strip()
                self.config_mgr.set("ntfy_topic", topic)
                self.notifier.update_credentials(
                    self.config_mgr.get("bot_token"),
                    self.config_mgr.get("chat_id"),
                    topic,
                )
                self.notifier.send_telegram_message(
                    f"🔔 ntfy 토픽이 <code>{topic}</code> 으로 설정되었습니다!\n"
                    f"안드로이드 ntfy 앱에서 <b>{topic}</b> 토픽을 구독하면 긴급 사이렌을 수신합니다."
                )

        elif cmd in ["/openrouter", "/set_openrouter", "/ai_key"]:
            if not arg:
                curr_key = self.config_mgr.get("openrouter_api_key", "")
                has_key = bool(curr_key and not curr_key.startswith("YOUR_"))
                masked = f"{curr_key[:10]}...{curr_key[-4:]}" if has_key else "미설정"
                status_info = self.summarizer.get_status()
                active_model = self.summarizer.get_active_model()
                latency = status_info.get("latency_seconds", 0.0)
                is_valid, val_msg = self.summarizer.validate_current_key()

                if has_key:
                    key_status_str = f"인증 성공 🟢 ({val_msg})" if is_valid else f"인증 실패 🔴 ({val_msg})"
                else:
                    key_status_str = "미설정 ⚪ (원문 발췌 모드)"

                self.notifier.send_telegram_message(
                    f"🤖 <b>OpenRouter 무료 모델 AI 브리핑 설정</b>\n\n"
                    f"• 엔진: <b>OpenRouter Free Models (비용: 0원)</b>\n"
                    f"• API 키 상태: {key_status_str}\n"
                    f"• 등록된 키: <code>{masked}</code>\n"
                    f"• 현재 적용 모델: <code>{active_model}</code>\n\n"
                    f"💡 <b>키 등록/변경 방법:</b>\n"
                    f"• <code>/openrouter &lt;API_KEY&gt;</code> (또는 채팅창에 <code>sk-or-...</code> 키 직접 전송)\n"
                    f"• <code>/models</code> : 무료 모델 평가 현황 및 핑 확인\n"
                    f"• <code>/openrouter clear</code> : 키 삭제"
                )
                return

            if arg.lower() in ["clear", "none", "삭제"]:
                self.config_mgr.set("openrouter_api_key", "")
                self.summarizer.update_api_key("")
                self.notifier.send_telegram_message("🤖 OpenRouter API 키가 삭제되었습니다. (기본 발췌 모드로 복귀)")
            else:
                self.config_mgr.set("openrouter_api_key", arg)
                self.summarizer.update_api_key(arg)
                is_valid, val_msg = validate_api_key(arg)
                if is_valid:
                    self.notifier.send_telegram_message(
                        f"✅ <b>OpenRouter API 키 설정 및 인증 성공! 🟢</b>\n\n"
                        f"• 상태: <b>{val_msg}</b>\n"
                        f"• 적용 모델: <code>{self.summarizer.get_active_model()}</code>\n\n"
                        "지금부터 모든 트윗과 뉴스에 한국어 구어체 브리핑이 실시간으로 제공됩니다."
                    )
                else:
                    self.notifier.send_telegram_message(
                        f"⚠️ <b>OpenRouter API 키 등록되었으나 인증 실패 🔴</b>\n\n"
                        f"• 오류 내용: <code>{val_msg}</code>\n\n"
                        "키가 올바른지 다시 확인해주세요."
                    )

        elif cmd in ["/models", "/ai_status", "/model"]:
            status_info = self.summarizer.get_status()
            active_model = self.summarizer.get_active_model()
            latency = status_info.get("latency_seconds", 0.0)
            last_eval = status_info.get("last_evaluated_at")
            candidates = status_info.get("candidates", [])
            provider = status_info.get("provider", "OpenRouter")
            last_err = status_info.get("last_error")

            has_key = bool(self.summarizer.api_key)
            is_valid, val_msg = self.summarizer.validate_current_key()
            masked_k = f"<code>{self.summarizer.api_key[:8]}...{self.summarizer.api_key[-4:]}</code>" if has_key else "미설정 ⚪"
            
            if has_key:
                auth_display = f"인증 성공 🟢 ({val_msg})" if is_valid else f"인증 실패 🔴 ({val_msg})"
            else:
                auth_display = "미등록 ⚪"

            last_eval_str = time.strftime("%H:%M:%S", time.localtime(last_eval)) if last_eval else "평가 진행 중"

            lines = [
                f"🤖 <b>AI 브리핑 엔진 실시간 상태 ({provider})</b>\n",
                f"• <b>API 키 연동:</b> {auth_display}",
                f"⭐️ <b>현재 선정된 활성 모델:</b>\n<code>{active_model}</code>",
            ]
            if last_err:
                lines.append(f"⚠️ <b>최근 오류:</b> <code>{last_err}</code>")

            if provider == "OpenRouter":
                lines.extend([
                    f"• 최근 응답 지연시간: <b>{latency}s</b>",
                    f"• 최근 평가 시각: <b>{last_eval_str}</b> (1시간 주기 자동 갱신)\n",
                    "📊 <b>상위 후보 모델 평가 결과:</b>",
                ])
                for c in candidates[:5]:
                    cid = c.get("id", "")
                    status = c.get("status", "")
                    score = c.get("score", 0)
                    prefix = "👉 " if cid == active_model else "  • "
                    lines.append(f"{prefix}<code>{cid}</code> : <b>{status}</b> (점수: {score})")
                lines.append("\n💡 <i>지금 즉시 재평가하려면 <code>/eval_models</code> 를 입력하세요.</i>")

            self.notifier.send_telegram_message("\n".join(lines))

        elif cmd in ["/eval_models", "/refresh_models"]:
            self.notifier.send_telegram_message("🔍 OpenRouter 무료 모델 평가 및 핑 테스트를 즉시 시작합니다...")
            def _run_eval():
                self.summarizer.force_evaluate()
                curr_status = self.summarizer.get_status()
                self.notifier.send_telegram_message(
                    f"✅ <b>모델 평가 완료!</b>\n"
                    f"선정된 활성 모델: <code>{curr_status['active_model']}</code> ({curr_status['latency_seconds']}s)"
                )
            threading.Thread(target=_run_eval, daemon=True).start()

        elif cmd in ["/test", "/live_test", "/check", "/check_all"]:
            threading.Thread(target=self.run_live_test_thread, daemon=True).start()

        elif cmd == "/status":
            users = self.config_mgr.get("monitored_users", [])
            seen_tweets = len(self.config_mgr.get("seen_tweet_ids", []))
            seen_news = len(self.config_mgr.get("seen_news_ids", []))
            uptime = int(time.time() - START_TIME)
            or_key = self.config_mgr.get("openrouter_api_key", "")
            has_ai = bool(or_key and not or_key.startswith("YOUR_"))
            status_info = self.summarizer.get_status()
            active_model = self.summarizer.get_active_model()
            latency = status_info.get("latency_seconds", 0.0)
            is_valid, _ = self.summarizer.validate_current_key()
            if has_ai:
                ai_status = f"ON [{active_model}] 🟢" if is_valid else "인증 실패 🔴 (/openrouter 로 재설정 필요)"
            else:
                ai_status = "OFF (기본 발췌) ⚪"
            self.notifier.send_telegram_message(
                f"🟢 <b>Alpha Terminal 정상 구동 중</b>\n\n"
                f"• 가동 시간(Uptime): {uptime}초\n"
                f"• 감시 중인 VIP 계정: {len(users)}개\n"
                f"• 누적 트윗 기록: {seen_tweets}개\n"
                f"• 누적 뉴스 기록: {seen_news}개\n"
                f"• AI 요약 엔진: {ai_status}\n"
                f"• 뉴스 피드 상태: {'ON 🟢' if self.config_mgr.get('enable_news_feed', True) else 'OFF 🔴'}\n"
                f"• 확인 주기: {self.config_mgr.get('check_interval_seconds', 30)}초"
            )


if __name__ == "__main__":
    bot = TwitterTelegramBot()
    bot.start()
