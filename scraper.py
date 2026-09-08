import html
import re
import urllib.parse
import urllib.request
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("x_scraper")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

# Regex to detect metrics at the end of tweet text (e.g. 1.3K, 321, 11K, 498K)
METRIC_PATTERN = re.compile(r"^(\d+(\.\d+)?[KkMmBb]?)$")


# Regex to match time strings like: 3h, 12m, 45s, Sep 6, Aug 30, 2d, 1y
TIME_PATTERN = re.compile(r"^(\d+[smhdwy]|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d{1,2}월|\d{1,2}일|\d{4}년)", re.IGNORECASE)


def parse_article_lines(article_html: str, username: str) -> Dict[str, str]:
    """Parse text lines from an <article> HTML fragment with precision."""
    cleaned = re.sub(r"<script.*?</script>", "", article_html, flags=re.DOTALL)
    cleaned = re.sub(r"<style.*?</style>", "", cleaned, flags=re.DOTALL)
    text_content = re.sub(r"<[^>]+>", "\n", cleaned)
    raw_lines = [html.unescape(line.strip()) for line in text_content.split("\n") if line.strip()]

    idx = 0
    # 1. Skip "Pinned" badge if present
    is_pinned = False
    if raw_lines and raw_lines[0].lower() in ["pinned", "고정됨", "고정 트윗", "pinned post"]:
        is_pinned = True
        idx += 1

    author = username
    time_str = ""

    # 2. Extract Author Name
    if idx < len(raw_lines) and not raw_lines[idx].startswith("@"):
        author = raw_lines[idx]
        idx += 1

    # 3. Skip @username
    if idx < len(raw_lines) and raw_lines[idx].startswith("@"):
        idx += 1

    # 4. Extract Timestamp
    if idx < len(raw_lines):
        candidate = raw_lines[idx]
        if TIME_PATTERN.match(candidate) or len(candidate) <= 8:
            time_str = candidate
            idx += 1

    # 5. Extract Tweet Body & Clean quote headers
    content_lines = []
    while idx < len(raw_lines):
        line = raw_lines[idx]
        if line.lower() in ["show more", "더 보기"]:
            idx += 1
            continue

        # Check for bottom metrics (replies, retweets, likes, views numbers)
        remaining = raw_lines[idx:]
        if all(METRIC_PATTERN.match(item) for item in remaining) and len(remaining) <= 6:
            break

        # Detect quoted tweet pattern: [Author, @handle, Time]
        if idx + 2 < len(raw_lines) and raw_lines[idx + 1].startswith("@") and (TIME_PATTERN.match(raw_lines[idx + 2]) or len(raw_lines[idx + 2]) <= 8):
            q_author = raw_lines[idx]
            q_handle = raw_lines[idx + 1]
            q_time = raw_lines[idx + 2]
            content_lines.append(f"\n[인용: {q_author} ({q_handle}) · {q_time}]")
            idx += 3
            continue

        # Detect raw quoted handle directly: [@handle, Time]
        if line.startswith("@") and idx + 1 < len(raw_lines) and TIME_PATTERN.match(raw_lines[idx + 1]):
            q_handle = line
            q_time = raw_lines[idx + 1]
            content_lines.append(f"\n[인용: {q_handle} · {q_time}]")
            idx += 2
            continue

        content_lines.append(line)
        idx += 1

    tweet_text = "\n".join(content_lines).strip()
    return {
        "author": author,
        "time": time_str,
        "text": tweet_text,
    }


def fetch_latest_tweets(username: str, timeout: int = 15) -> List[Dict[str, str]]:
    """
    Fetch recent tweets from an X user profile using free SSR HTML scraping.
    Returns a list of dictionaries with keys: id, username, author, text, url, time.
    """
    # Normalize username (remove @ or url)
    username = username.strip().lstrip("@")
    if "/" in username:
        username = username.rstrip("/").split("/")[-1].split("?")[0]
        
    url = f"https://x.com/{username}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            page_html = response.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logger.error(f"Error fetching X profile for @{username}: {e}")
        return []

    # Find each block containing tweet link and article
    # Format: data-href="/username/status/TWEET_ID" ... <article ... </article>
    # Or find all data-href / href containing /status/
    tweet_blocks = re.findall(
        r'(?:data-href|href)=["\'](?:/[^/]+)?/status/(\d+)["\'][^>]*>.*?<article[^>]*>(.*?)</article>',
        page_html,
        re.DOTALL,
    )
    
    results = []
    seen_in_page = set()

    for tweet_id, article_html in tweet_blocks:
        if tweet_id in seen_in_page:
            continue
        seen_in_page.add(tweet_id)
        
        parsed = parse_article_lines(article_html, username)
        results.append({
            "id": tweet_id,
            "username": username,
            "author": parsed["author"],
            "time": parsed["time"],
            "text": parsed["text"],
            "url": f"https://x.com/{username}/status/{tweet_id}",
        })

    # Fallback if regular pattern didn't capture (e.g. slight layout change)
    if not results:
        status_ids = re.findall(rf'/{re.escape(username)}/status/(\d+)', page_html)
        unique_ids = []
        for sid in status_ids:
            if sid not in unique_ids:
                unique_ids.append(sid)
                
        articles = re.findall(r'<article[^>]*>(.*?)</article>', page_html, re.DOTALL)
        for i, article_html in enumerate(articles):
            tweet_id = unique_ids[i] if i < len(unique_ids) else f"unknown_{i}"
            parsed = parse_article_lines(article_html, username)
            results.append({
                "id": tweet_id,
                "username": username,
                "author": parsed["author"],
                "time": parsed["time"],
                "text": parsed["text"],
                "url": f"https://x.com/{username}/status/{tweet_id}",
            })

    return results


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "thsottiaux"
    print(f"Testing fetch for @{target}...")
    tweets = fetch_latest_tweets(target)
    print(f"Found {len(tweets)} tweets:\n")
    for t in tweets:
        print(f"[{t['id']}] {t['author']} ({t['time']}):")
        print(t['text'])
        print(f"URL: {t['url']}\n" + "-" * 50)
