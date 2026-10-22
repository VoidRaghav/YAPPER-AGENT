import random
from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.db import BotLine, Group, Trend, db
from app.embeddings import Embedder
from app.trends import sources, store
from app.trends.store import Card
from app.utils import utcnow

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss"><channel>
<item><title>moye moye</title><link>https://x</link><ht:approx_traffic>200K+</ht:approx_traffic><ht:news_item><ht:news_item_title>Moye moye trend explained</ht:news_item_title></ht:news_item></item>
<item><title>ind vs aus</title><link>https://y</link></item>
</channel></rss>"""


def card(name: str, category: str = "meme", safe: bool = True, evergreen: bool = False, context: str = "viral reel") -> Card:
    return Card(name=name, phrase=name, context=context, usage="when plans fail", tags=["test"], category=category, safe=safe, evergreen=evergreen)


async def test_google_trends_parsing():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=RSS.encode()))
    async with httpx.AsyncClient(transport=transport) as client:
        items = await sources.google_trends(client)
    assert [i.title for i in items] == ["moye moye", "ind vs aus"]
    assert "200K+" in items[0].snippet


async def test_reddit_parsing_filters_nsfw():
    payload = {"data": {"children": [{"data": {"title": "desi meme", "score": 900, "permalink": "/r/x/1"}}, {"data": {"title": "nsfw", "over_18": True}}]}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        items = await sources.reddit(client, ["IndianDankMemes"])
    assert [i.title for i in items] == ["desi meme"]


async def test_seed_and_upsert_and_safety(database, settings):
    now = utcnow()
    async with db.session() as ses:
        added = await store.load_seeds(ses, Embedder(), now)
        assert added > 50
        assert await store.load_seeds(ses, Embedder(), now) == 0
        n = await store.upsert_cards(
            ses,
            [card("new reel trend"), card("election memes", category="politics"), card("stampede video", context="crowd tragedy"), card("new reel trend")],
            "feeds", Embedder(), now, settings,
        )
        assert n == 3
    async with db.session() as ses:
        unsafe = (await ses.scalars(select(Trend.name).where(Trend.safe.is_(False)))).all()
        assert set(unsafe) == {"election memes", "stampede video"}
        assert await ses.scalar(select(func.count(Trend.id)).where(Trend.name == "new reel trend")) == 1


async def test_pick_prefers_relevant_and_fresh_and_skips_recently_used(database, settings):
    now = utcnow()
    async with db.session() as ses:
        await store.load_seeds(ses, Embedder(), now)
        await store.upsert_cards(ses, [card("fresh one"), card("fresh two")], "feeds", Embedder(), now, settings)
        g = Group(wa_jid="g1@g.us")
        ses.add(g)
        await ses.flush()
        ses.add(BotLine(group_id=g.id, ts=now - timedelta(hours=1), text="x", trend_ref="goa plan"))
        await ses.flush()
        picked = await store.pick(ses, settings, g.id, None, "goa plan trip cancel again late", now, random.Random(1))
    names = [t.name for t in picked]
    assert len(picked) == settings.trends_k
    assert "goa plan" not in names
    assert sum(1 for t in picked if not t.evergreen) >= 2
    assert "trending this week" in store.format_trends(picked, now)
