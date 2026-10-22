import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import BotLine, Trend
from app.embeddings import Embedder
from app.safety import trend_is_safe
from app.utils import slug, words

log = logging.getLogger(__name__)

SEED_PATH = Path(__file__).resolve().parent / "seed_memes.json"
Category = Literal["meme", "bollywood", "cricket", "tv", "viral_video", "news_funny", "linkedin_cringe", "politics", "tragedy", "other"]


class Card(BaseModel):
    name: str
    phrase: str
    context: str
    usage: str
    tags: list[str]
    category: Category
    safe: bool
    evergreen: bool


def card_text(t: Trend | Card) -> str:
    return f"{t.name}. {t.phrase}. {t.context}. {t.usage}. {' '.join(t.tags)}"


async def upsert_cards(
    ses: AsyncSession, cards: list[Card], source: str, embedder: Embedder, now: datetime, settings: Settings, url: str = ""
) -> int:
    added = 0
    fresh: list[Trend] = []
    for card in cards:
        if not card.name.strip():
            continue
        verdict = trend_is_safe(card_text(card), settings.trends_allow_politics)
        blocked = card.category == "tragedy" or (card.category == "politics" and not settings.trends_allow_politics)
        safe = card.safe and verdict.ok and not blocked
        key = slug(card.name)
        existing = await ses.scalar(select(Trend).where(Trend.key == key))
        expires = None if card.evergreen else now + timedelta(days=settings.trends_ttl_days)
        if existing:
            existing.last_seen = now
            existing.score += 1
            if not existing.evergreen:
                existing.expires_at = expires
            existing.safe = existing.safe and safe
            continue
        trend = Trend(
            key=key,
            name=card.name.strip()[:200],
            phrase=card.phrase.strip(),
            context=card.context.strip(),
            usage=card.usage.strip(),
            tags=[t.strip().lower() for t in card.tags][:8],
            category=card.category,
            source=source,
            url=url,
            evergreen=card.evergreen,
            safe=safe,
            score=1,
            first_seen=now,
            last_seen=now,
            expires_at=expires,
        )
        ses.add(trend)
        fresh.append(trend)
        added += 1
    if fresh:
        vectors = await embedder.embed([card_text(t) for t in fresh])
        if vectors:
            for t, v in zip(fresh, vectors, strict=True):
                t.embedding = v
    return added


async def load_seeds(ses: AsyncSession, embedder: Embedder, now: datetime) -> int:
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    existing = set((await ses.scalars(select(Trend.key).where(Trend.source == "seed"))).all())
    fresh: list[Trend] = []
    for item in items:
        key = slug(item["name"])
        if key in existing:
            continue
        trend = Trend(
            key=key,
            name=item["name"],
            phrase=item["phrase"],
            context=item["context"],
            usage=item["usage"],
            tags=item.get("tags", []),
            category=item.get("category", "meme"),
            source="seed",
            evergreen=True,
            safe=True,
            score=0.5,
            first_seen=now,
            last_seen=now,
        )
        ses.add(trend)
        fresh.append(trend)
    if fresh:
        vectors = await embedder.embed([card_text(t) for t in fresh])
        if vectors:
            for t, v in zip(fresh, vectors, strict=True):
                t.embedding = v
    return len(fresh)


async def backfill_embeddings(ses: AsyncSession, embedder: Embedder) -> int:
    if not embedder.enabled:
        return 0
    rows = (await ses.scalars(select(Trend).where(Trend.embedding.is_(None)).limit(500))).all()
    vectors = await embedder.embed([card_text(t) for t in rows]) if rows else None
    if not vectors:
        return 0
    for t, v in zip(rows, vectors, strict=True):
        t.embedding = v
    return len(rows)


async def prune(ses: AsyncSession, now: datetime) -> None:
    await ses.execute(delete(Trend).where(Trend.evergreen.is_(False), Trend.expires_at < now - timedelta(days=30)))


async def pick(
    ses: AsyncSession,
    settings: Settings,
    group_id: int,
    qvec: np.ndarray | None,
    text: str,
    now: datetime,
    rng: random.Random,
) -> list[Trend]:
    k = settings.trends_k
    cutoff = now - timedelta(hours=settings.trends_reuse_cooldown_hours)
    used = set(
        (await ses.scalars(select(BotLine.trend_ref).where(BotLine.group_id == group_id, BotLine.ts >= cutoff, BotLine.trend_ref != ""))).all()
    )
    used_low = {u.lower() for u in used}
    active = (
        await ses.scalars(select(Trend).where(Trend.safe.is_(True), or_(Trend.evergreen.is_(True), Trend.expires_at > now)))
    ).all()
    pool = [t for t in active if t.name.lower() not in used_low]
    if not pool:
        return []
    q = words(text)
    scored = []
    for t in pool:
        if qvec is not None and t.embedding is not None and t.embedding.shape == qvec.shape:
            rel = float(np.dot(t.embedding, qvec))
        else:
            rel = len(q & words(card_text(t))) / (len(q) or 1)
        is_fresh = not t.evergreen and now - t.first_seen < timedelta(days=4)
        scored.append((rel + (0.12 if is_fresh else 0) + min(t.score, 5) * 0.01 + rng.random() * 0.03, t))
    scored.sort(key=lambda x: -x[0])
    top = [t for _, t in scored[:k]]
    fresh_pool = [t for _, t in scored if not t.evergreen and t not in top]
    classic_slots = [i for i, t in enumerate(top) if t.evergreen]
    while sum(1 for t in top if not t.evergreen) < 2 and fresh_pool and classic_slots:
        top[classic_slots.pop()] = fresh_pool.pop(0)
    return top


def format_trends(trends: list[Trend], now: datetime) -> str:
    if not trends:
        return "(nothing fits right now)"
    lines = []
    for t in trends:
        tag = "trending this week" if not t.evergreen and now - t.first_seen < timedelta(days=7) else "classic"
        lines.append(f'- {t.name} ({tag}): "{t.phrase}". {t.context}. use when: {t.usage}')
    return "\n".join(lines)
