import hashlib
import html
import logging
import re
import urllib.parse
import urllib.request
from typing import List, Dict, Any

logger = logging.getLogger("x_news_collector")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/xml,text/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
}


def clean_cdata(text: str) -> str:
    if not text:
        return ""
    # Unescape first so that &lt;tag&gt; entities become actual tags to be cleanly stripped
    text = html.unescape(text)
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def make_id(unique_string: str) -> str:
    return hashlib.sha256(unique_string.encode("utf-8")).hexdigest()[:16]


def fetch_arxiv_ai_papers(limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch latest AI papers directly from ArXiv official API."""
    url = f"http://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    results = []

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_text = resp.read().decode("utf-8", errors="ignore")

        entries = re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL)
        for entry in entries:
            title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            id_m = re.search(r"<id>(.*?)</id>", entry, re.DOTALL)
            pub_m = re.search(r"<published>(.*?)</published>", entry, re.DOTALL)

            if title_m and id_m:
                title = clean_cdata(title_m.group(1))
                summary = clean_cdata(summary_m.group(1)) if summary_m else ""
                url_str = id_m.group(1).strip()
                published = pub_m.group(1).strip() if pub_m else ""

                results.append({
                    "id": make_id(url_str),
                    "category": "🤖 AI 원천 논문",
                    "source": "ArXiv CS.AI",
                    "title": title,
                    "summary": summary[:400] + ("..." if len(summary) > 400 else ""),
                    "url": url_str,
                    "published": published,
                })
    except Exception as e:
        logger.error(f"Error fetching ArXiv AI papers: {e}")

    return results


def fetch_techcrunch_ai_news(limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch latest AI industry news from TechCrunch AI feed."""
    url = "https://techcrunch.com/category/artificial-intelligence/feed/"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    results = []

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_text = resp.read().decode("utf-8", errors="ignore")

        items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
        for item in items[:limit]:
            title_m = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
            link_m = re.search(r"<link>(.*?)</link>", item, re.DOTALL)
            desc_m = re.search(r"<description>(.*?)</description>", item, re.DOTALL)
            pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item, re.DOTALL)

            if title_m and link_m:
                title = clean_cdata(title_m.group(1))
                link_str = link_m.group(1).strip()
                summary = clean_cdata(desc_m.group(1)) if desc_m else ""
                published = pub_m.group(1).strip() if pub_m else ""

                results.append({
                    "id": make_id(link_str),
                    "category": "⚡ 글로벌 테크 & AI",
                    "source": "TechCrunch AI",
                    "title": title,
                    "summary": summary[:400] + ("..." if len(summary) > 400 else ""),
                    "url": link_str,
                    "published": published,
                })
    except Exception as e:
        logger.error(f"Error fetching TechCrunch AI news: {e}")

    return results


def fetch_korea_policy_news(queries: List[str] = None, limit_per_query: int = 4) -> List[Dict[str, Any]]:
    """
    Fetch primary economic policy & Commercial Act (상법) news via real-time Google News RSS.
    """
    if queries is None:
        queries = ["상법 개정", "경제정책 금융위원회"]

    results = []
    seen_urls = set()

    for q in queries:
        try:
            encoded_query = urllib.parse.quote(q)
            url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                xml_text = resp.read().decode("utf-8", errors="ignore")

            items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)
            for item in items[:limit_per_query]:
                title_m = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
                link_m = re.search(r"<link>(.*?)</link>", item, re.DOTALL)
                pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item, re.DOTALL)
                desc_m = re.search(r"<description>(.*?)</description>", item, re.DOTALL)

                if title_m and link_m:
                    link_str = link_m.group(1).strip()
                    if link_str in seen_urls:
                        continue
                    seen_urls.add(link_str)

                    title = clean_cdata(title_m.group(1))
                    summary = clean_cdata(desc_m.group(1)) if desc_m else ""
                    published = pub_m.group(1).strip() if pub_m else ""

                    category = "🏛️ 경제 정책 · 상법" if "상법" in q else "📈 금융 & 경제 정책"
                    results.append({
                        "id": make_id(link_str),
                        "category": category,
                        "source": "정책 & 경제 속보",
                        "title": title,
                        "summary": summary[:400] + ("..." if len(summary) > 400 else ""),
                        "url": link_str,
                        "published": published,
                    })
        except Exception as e:
            logger.error(f"Error fetching policy news for query '{q}': {e}")

    return results


def fetch_all_curated_news() -> List[Dict[str, Any]]:
    """Fetch curated items across AI, IT Tech, and Economic / Commercial law policy."""
    all_items = []
    # 1. Economic / Commercial Law (상법)
    all_items.extend(fetch_korea_policy_news(["상법 개정", "자본시장법 금융위원회", "공정거래위원회"]))
    # 2. ArXiv AI
    all_items.extend(fetch_arxiv_ai_papers(limit=3))
    # 3. TechCrunch AI
    all_items.extend(fetch_techcrunch_ai_news(limit=3))
    return all_items


if __name__ == "__main__":
    import sys
    # Reconfigure stdout to utf-8 if possible
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("Testing fetch_all_curated_news()...")
    news = fetch_all_curated_news()
    print(f"Total fetched: {len(news)} items\n")
    for idx, item in enumerate(news[:5]):
        cat = item.get("category", "")
        src = item.get("source", "")
        title = item.get("title", "")
        print(f"[{cat}] ({src})")
        print(f"Title: {title}")
        print(f"Link: {item['url']}")
        print(f"Date: {item['published']}")
        print("-" * 50)

