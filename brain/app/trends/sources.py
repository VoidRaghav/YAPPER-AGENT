import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

import feedparser
import httpx

from app.config import Settings

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
INDIA_WOEID = 23424848


@dataclass
class RawItem:
    source: str
    title: str
    snippet: str = ""
    url: str = ""
    score: float = 0.0

    def line(self) -> str:
        extra = f" | {self.snippet}" if self.snippet else ""
        return f"[{self.source}] {self.title}{extra}"


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response | None:
    try:
        res = await client.get(url, **kwargs)
        if res.status_code == 200:
            return res
        log.info("trend source %s returned %s", url.split("?")[0], res.status_code)
    except httpx.HTTPError as e:
        log.info("trend source %s failed: %s", url.split("?")[0], e)
    return None


async def google_trends(client: httpx.AsyncClient) -> list[RawItem]:
    res = await _get(client, "https://trends.google.com/trending/rss?geo=IN")
    if not res:
        return []
    feed = feedparser.parse(res.content)
    items = []
    for e in feed.entries[:30]:
        traffic = e.get("ht_approx_traffic", "")
        news = e.get("ht_news_item_title", "")
        snippet = f"{news} ({traffic} searches)" if traffic else news
        items.append(RawItem("google_trends", e.get("title", ""), snippet, e.get("link", "")))
    return items


async def google_news(client: httpx.AsyncClient, queries: list[str]) -> list[RawItem]:
    async def one(q: str) -> list[RawItem]:
        url = f"https://news.google.com/rss/search?q={quote_plus(q + ' when:7d')}&hl=en-IN&gl=IN&ceid=IN:en"
        res = await _get(client, url)
        if not res:
            return []
        feed = feedparser.parse(res.content)
        return [RawItem("news", e.get("title", ""), q, e.get("link", "")) for e in feed.entries[:12]]

    batches = await asyncio.gather(*(one(q) for q in queries))
    return [item for batch in batches for item in batch]


async def reddit(client: httpx.AsyncClient, subs: list[str]) -> list[RawItem]:
    async def one(sub: str) -> list[RawItem]:
        res = await _get(client, f"https://www.reddit.com/r/{sub}/top.json?t=week&limit=25&raw_json=1")
        if res:
            posts = [c.get("data", {}) for c in res.json().get("data", {}).get("children", [])]
            return [
                RawItem("reddit", p.get("title", ""), f"r/{sub}, {p.get('score', 0)} upvotes", "https://reddit.com" + p.get("permalink", ""), float(p.get("score", 0)))
                for p in posts
                if not p.get("over_18") and not p.get("stickied") and p.get("title")
            ]
        rss = await _get(client, f"https://www.reddit.com/r/{sub}/top/.rss?t=week")
        if not rss:
            return []
        feed = feedparser.parse(rss.content)
        return [RawItem("reddit", e.get("title", ""), f"r/{sub}", e.get("link", "")) for e in feed.entries[:25]]

    batches = await asyncio.gather(*(one(s) for s in subs))
    return [item for batch in batches for item in batch]


async def x_trends(client: httpx.AsyncClient, token: str) -> list[RawItem]:
    if not token:
        return []
    res = await _get(client, f"https://api.x.com/2/trends/by/woeid/{INDIA_WOEID}", headers={"Authorization": f"Bearer {token}"})
    if not res:
        return []
    return [
        RawItem("x", t.get("trend_name", ""), f"{t.get('tweet_count', '')} posts".strip(), "", float(t.get("tweet_count") or 0))
        for t in res.json().get("data", [])[:40]
        if t.get("trend_name")
    ]


async def collect(settings: Settings) -> list[RawItem]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
        results = await asyncio.gather(
            google_trends(client),
            google_news(client, settings.news_queries),
            reddit(client, settings.reddit_subs),
            x_trends(client, settings.x_bearer_token),
            return_exceptions=True,
        )
    items: list[RawItem] = []
    for r in results:
        if isinstance(r, BaseException):
            log.warning("trend source crashed: %s", r)
        else:
            items.extend(r)
    seen: set[str] = set()
    unique = []
    for item in items:
        key = item.title.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    log.info("collected %d raw trend items", len(unique))
    return unique
