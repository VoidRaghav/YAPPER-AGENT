import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app import prompts
from app.db import db
from app.trends.sources import RawItem, collect
from app.trends.store import Card, backfill_embeddings, load_seeds, prune, upsert_cards
from app.utils import to_local, utcnow

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)


class Cards(BaseModel):
    cards: list[Card]


async def distill(rt: "Runtime", items: list[RawItem], batch: int = 60) -> list[Card]:
    s = rt.s
    cards: list[Card] = []
    for i in range(0, len(items), batch):
        chunk = items[i : i + batch]
        out = await rt.llm.parse(
            Cards,
            model=s.trends_model,
            system=prompts.render("trends_distill"),
            prompt="\n".join(item.line() for item in chunk),
            effort=s.fast_effort,
            max_tokens=8000,
        )
        if out:
            cards.extend(out.cards)
    return cards


async def scout(rt: "Runtime") -> list[RawItem]:
    s = rt.s
    notes = await rt.llm.research(
        model=s.scout_model,
        system=prompts.render("trends_scout_system", BOT_NAME=s.bot_name),
        prompt=prompts.render("trends_scout", DATE=to_local(utcnow(), s.timezone).strftime("%d %B %Y")),
    )
    if not notes:
        return []
    return [RawItem("web_scout", block.strip(), "") for block in notes.split("\n\n") if len(block.strip()) > 20]


async def seed(rt: "Runtime") -> None:
    async with db.session() as ses:
        added = await load_seeds(ses, rt.embedder, utcnow())
        filled = await backfill_embeddings(ses, rt.embedder)
    if added or filled:
        log.info("meme bank: %d seeds added, %d embeddings filled", added, filled)


async def refresh(rt: "Runtime", with_scout: bool = False) -> int:
    s = rt.s
    batches: dict[str, list[Card]] = {"feeds": await distill(rt, await collect(s))}
    if with_scout and s.trends_scout_enabled:
        scouted = await scout(rt)
        log.info("web scout returned %d notes", len(scouted))
        batches["web_scout"] = await distill(rt, scouted)
    now = utcnow()
    added = 0
    async with db.session() as ses:
        for source, cards in batches.items():
            added += await upsert_cards(ses, cards, source, rt.embedder, now, s)
        await prune(ses, now)
    log.info("meme bank refresh: %s distilled, %d new", {k: len(v) for k, v in batches.items()}, added)
    return added
