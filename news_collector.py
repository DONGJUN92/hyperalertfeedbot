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


EXCLUDE_ENTERTAINMENT_KEYWORDS = [
    "방송", "출연", "예능", "아이돌", "가수", "배우", "드라마", "영화", "포맨", "돌싱",
    "음원", "소속사", "연습생", "유튜브", "스포츠", "축구", "야구", "골프", "연예", "화보",
    "미담", "투병", "암 투병", "결혼", "이혼", "열애", "음주", "폭행", "마약", "연예인",
    "팬미팅", "콘서트", "빌보드", "음반", "뮤직", "시청률", "예고편", "피소", "피의자",
    "스캔들", "사생활", "조권", "김혜수", "세바퀴", "먹방", "인터뷰",
]


def fetch_korea_policy_news(queries: List[str] = None, limit_per_query: int = 4) -> List[Dict[str, Any]]:
    """
    Fetch primary economic policy & Commercial Act (상법) news with rich real-time summaries.
    Uses Daum News real-time search to guarantee authentic Korean lead paragraphs/abstracts for AI summarization.
    """
    if queries is None:
        queries = ["상법 개정", "자본시장법 금융위", "공정거래위원회 기업결합", "공정거래법 개정"]

    results = []
    seen_urls = set()

    for q in queries:
        try:
            url = "https://search.daum.net/search?w=news&sort=recency&q=" + urllib.parse.quote(q)
            req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html_text = resp.read().decode("utf-8", errors="ignore")

            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_text, "html.parser")
                items = soup.find_all("div", class_="c-item-content")
                count = 0
                for item in items:
                    t_el = item.find("strong", class_="tit-g")
                    d_el = item.find("p", class_="conts-desc")
                    a_el = item.find("a", class_="tit-g") or item.find("a", class_="thumb_bf") or (t_el.find_parent("a") if t_el else None) or item.find("a")
                    sub_el = item.find("span", class_="gem-subinfo") or item.find("span", class_="txt_info")

                    if t_el:
                        link_str = a_el.get("href", "").strip() if a_el else ""
                        if not link_str or link_str in seen_urls:
                            continue

                        title = clean_cdata(t_el.get_text(strip=True))
                        summary = clean_cdata(d_el.get_text(strip=True)) if d_el else ""
                        pub_date = clean_cdata(sub_el.get_text(strip=True)) if sub_el else ""

                        # Filter out entertainment, celebrity gossip, and tabloid noise
                        full_content_lower = f"{title} {summary}".lower()
                        if any(ex in full_content_lower for ex in EXCLUDE_ENTERTAINMENT_KEYWORDS):
                            continue

                        seen_urls.add(link_str)

                        if "상법" in q:
                            category = "🏛️ 경제 정책 · 상법"
                        elif "공정" in q:
                            category = "⚖️ 공정위 & 규제 정책"
                        else:
                            category = "📈 금융 & 경제 정책"

                        results.append({
                            "id": make_id(link_str),
                            "category": category,
                            "source": "정책 & 경제 속보",
                            "title": title,
                            "summary": summary,
                            "url": link_str,
                            "published": pub_date,
                        })
                        count += 1
                        if count >= limit_per_query:
                            break
            except Exception as parse_err:
                logger.debug(f"Daum soup parse error: {parse_err}")

        except Exception as e:
            logger.error(f"Error fetching policy news for query '{q}': {e}")

    return results


def fetch_geeknews_full_body(topic_url: str) -> str:
    """Fetch the full, untruncated topic body directly from GeekNews article page."""
    if not topic_url or "news.hada.io/topic" not in topic_url:
        return ""
    try:
        req = urllib.request.Request(topic_url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=5) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r"<section id=['\"]topic_contents['\"][^>]*>(.*?)</section>", html_text, re.DOTALL)
        if m:
            raw = m.group(1)
            raw = re.sub(r"<li>(.*?)</li>", r"• \1\n", raw, flags=re.DOTALL)
            raw = re.sub(r"</p>", r"\n", raw)
            clean = clean_cdata(raw)
            if clean and len(clean) > 30:
                return clean
    except Exception as e:
        logger.debug(f"Failed to fetch GeekNews full body for {topic_url}: {e}")
    return ""


def fetch_geeknews(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Fetch latest IT/Tech/Startup curated posts from GeekNews (https://news.hada.io/rss/news).
    Always parsed and notified without requiring keyword matching.
    """
    url = "https://news.hada.io/rss/news"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    results = []

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_text = resp.read().decode("utf-8", errors="ignore")

        entries = re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL)
        for entry in entries[:limit]:
            title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            link_m = re.search(r"href=['\"]([^'\"]+)['\"]", entry)
            id_m = re.search(r"<id>(.*?)</id>", entry, re.DOTALL)
            content_m = re.search(r"<content[^>]*>(.*?)</content>", entry, re.DOTALL)
            summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            pub_m = re.search(r"<published>(.*?)</published>", entry, re.DOTALL)
            upd_m = re.search(r"<updated>(.*?)</updated>", entry, re.DOTALL)

            if title_m:
                title = clean_cdata(title_m.group(1))
                link_str = link_m.group(1).strip() if link_m else (id_m.group(1).strip() if id_m else "https://news.hada.io")
                pub_date = pub_m.group(1).strip() if pub_m else (upd_m.group(1).strip() if upd_m else "")

                # Fetch full article body directly to avoid GeekNews RSS snippet truncation
                full_body = fetch_geeknews_full_body(link_str) if "news.hada.io/topic" in link_str else ""
                if full_body:
                    summary = full_body
                else:
                    raw_body = content_m.group(1) if content_m else (summary_m.group(1) if summary_m else "")
                    summary = clean_cdata(raw_body)

                results.append({
                    "id": make_id(link_str),
                    "category": "긱뉴스 (GeekNews)",
                    "source": "GeekNews",
                    "title": title,
                    "summary": summary[:1200] + ("..." if len(summary) > 1200 else ""),
                    "url": link_str,
                    "published": pub_date,
                    "always_notify": True,
                })
    except Exception as e:
        logger.error(f"Error fetching GeekNews: {e}")

    return results



def fetch_all_curated_news() -> List[Dict[str, Any]]:
    """Fetch curated items across AI, IT Tech, Economic / Commercial law policy, and GeekNews."""
    all_items = []
    # 1. Economic / Commercial Law (상법) & Antitrust (공정거래)
    all_items.extend(fetch_korea_policy_news(["상법 개정", "자본시장법 금융위", "공정거래위원회 기업결합", "공정거래법 개정"]))
    # 2. GeekNews (https://news.hada.io/) - notify on every new post
    all_items.extend(fetch_geeknews(limit=5))
    # 3. ArXiv AI
    all_items.extend(fetch_arxiv_ai_papers(limit=3))
    # 4. TechCrunch AI
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

