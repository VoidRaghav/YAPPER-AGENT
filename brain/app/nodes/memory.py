import logging
from typing import TYPE_CHECKING, Literal

import numpy as np
from pydantic import BaseModel
from sqlalchemy import delete, select

from app import prompts
from app.db import Group, Lore, Memory, Message, Person, db
from app.repo import people_map, resolve_person, transcript
from app.safety import sensitive_categories
from app.utils import utcnow

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

GROUP_WORDS = {"", "group", "everyone", "all", "sab", "the group"}


class XMemory(BaseModel):
    person: str
    kind: Literal["fact", "habit", "bit", "moment"]
    text: str
    sensitive: bool


class XLore(BaseModel):
    title: str
    description: str


class XNick(BaseModel):
    person: str
    nickname: str


class XProfile(BaseModel):
    person: str
    summary: str


class Extraction(BaseModel):
    memories: list[XMemory]
    lore: list[XLore]
    nicknames: list[XNick]
    profiles: list[XProfile]


def opted_out(p: Person | None) -> bool:
    return bool(p and (p.profile_json or {}).get("opt_out"))


async def extract(rt: "Runtime", group_id: int) -> int:
    if group_id in rt.extracting:
        return 0
    rt.extracting.add(group_id)
    try:
        return await _extract(rt, group_id)
    finally:
        rt.extracting.discard(group_id)


async def _extract(rt: "Runtime", group_id: int) -> int:
    s = rt.s
    async with db.session() as ses:
        group = await ses.get(Group, group_id)
        people = await people_map(ses, group_id)
        msgs = (
            await ses.scalars(
                select(Message)
                .where(Message.group_id == group_id, Message.id > group.memory_cursor, Message.type != "reaction")
                .order_by(Message.id)
                .limit(s.memory_batch_max)
            )
        ).all()
        if not msgs:
            group.msgs_since_memory = 0
            return 0
        cursor = msgs[-1].id
        lore_titles = (await ses.scalars(select(Lore.title).where(Lore.group_id == group_id).order_by(Lore.id.desc()).limit(40))).all()
        known = (await ses.scalars(select(Memory.text).where(Memory.group_id == group_id).order_by(Memory.id.desc()).limit(40))).all()
        roster = "\n".join(
            f"- {p.display_name}{' (aka ' + ', '.join(p.nicknames) + ')' if p.nicknames else ''}: {(p.profile_json or {}).get('summary') or '(no profile yet)'}"
            for p in people.values()
            if not opted_out(p)
        )
        convo = transcript(list(msgs), people, s.bot_name, s.timezone)
        to_embed = [(m.id, m.content) for m in msgs if not m.from_bot and len(m.content.split()) >= 4]

    out = await rt.llm.parse(
        Extraction,
        model=s.memory_model,
        system=prompts.render("memory", BOT_NAME=s.bot_name),
        prompt=prompts.render(
            "memory_turn",
            PEOPLE=roster or "(nobody yet)",
            LORE="\n".join(f"- {t}" for t in lore_titles) or "(none yet)",
            KNOWN="\n".join(f"- {t}" for t in known) or "(none yet)",
            TRANSCRIPT=convo,
        ),
        effort=s.fast_effort,
        max_tokens=8000,
    )
    msg_vectors = await rt.embedder.embed([t for _, t in to_embed]) if to_embed else None
    mem_vectors = await rt.embedder.embed([m.text for m in out.memories]) if out and out.memories else None
    lore_vectors = await rt.embedder.embed([f"{x.title}. {x.description}" for x in out.lore]) if out and out.lore else None

    stored = 0
    async with db.session() as ses:
        group = await ses.get(Group, group_id)
        group.memory_cursor, group.msgs_since_memory, group.memory_at = cursor, 0, utcnow()
        if msg_vectors:
            for (mid, _), vec in zip(to_embed, msg_vectors, strict=True):
                m = await ses.get(Message, mid)
                if m:
                    m.embedding = vec
        if out:
            stored += await _store_memories(ses, group_id, out.memories, mem_vectors, [m.id for m in msgs][-20:])
            await _store_lore(ses, group_id, out.lore, lore_vectors)
            await _store_people(ses, group_id, out.nicknames, out.profiles)
    log.info("memory: group %s processed %d msgs, stored %d memories", group_id, len(msgs), stored)
    return stored


async def _store_memories(ses, group_id: int, items: list[XMemory], vectors, source_ids: list[int]) -> int:
    stored = 0
    for i, item in enumerate(items):
        text = item.text.strip()
        if not text:
            continue
        person = None if item.person.strip().lower() in GROUP_WORDS else await resolve_person(ses, group_id, item.person)
        if opted_out(person):
            continue
        vec = vectors[i] if vectors else None
        same = select(Memory).where(Memory.group_id == group_id, Memory.person_id == (person.id if person else None))
        existing = (await ses.scalars(same.order_by(Memory.id.desc()).limit(300))).all()
        if any(e.text.lower() == text.lower() for e in existing):
            continue
        if vec is not None and any(e.embedding is not None and e.embedding.shape == vec.shape and float(np.dot(e.embedding, vec)) > 0.93 for e in existing):
            continue
        ses.add(
            Memory(
                group_id=group_id,
                person_id=person.id if person else None,
                kind=item.kind,
                text=text[:500],
                embedding=vec,
                sensitive=item.sensitive or bool(sensitive_categories(text)),
                source_msg_ids=source_ids,
            )
        )
        stored += 1
    return stored


async def _store_lore(ses, group_id: int, items: list[XLore], vectors) -> None:
    for i, item in enumerate(items):
        title = item.title.strip()[:200]
        if not title or sensitive_categories(f"{title} {item.description}"):
            continue
        existing = await ses.scalar(select(Lore).where(Lore.group_id == group_id, Lore.title.ilike(title)))
        if existing:
            existing.description = item.description.strip()
            continue
        ses.add(Lore(group_id=group_id, title=title, description=item.description.strip(), embedding=vectors[i] if vectors else None))


async def _store_people(ses, group_id: int, nicks: list[XNick], profiles: list[XProfile]) -> None:
    for n in nicks:
        person = await resolve_person(ses, group_id, n.person)
        nick = n.nickname.strip().lower()
        if person and nick and not opted_out(person) and nick not in person.nicknames and not sensitive_categories(nick):
            person.nicknames = [*person.nicknames, nick][-8:]
    for p in profiles:
        person = await resolve_person(ses, group_id, p.person)
        if person and p.summary.strip() and not opted_out(person):
            person.profile_json = {**(person.profile_json or {}), "summary": p.summary.strip()[:600], "updated": utcnow().isoformat()}


async def forget_person(ses, person: Person) -> int:
    res = await ses.execute(delete(Memory).where(Memory.person_id == person.id))
    person.nicknames = []
    person.profile_json = {"opt_out": True}
    return res.rowcount or 0


async def consolidate(rt: "Runtime", group_id: int) -> int:
    removed = 0
    async with db.session() as ses:
        rows = (await ses.scalars(select(Memory).where(Memory.group_id == group_id, Memory.embedding.is_not(None)).order_by(Memory.id.desc()).limit(2000))).all()
        by_person: dict[int | None, list[Memory]] = {}
        for m in rows:
            by_person.setdefault(m.person_id, []).append(m)
        for items in by_person.values():
            kept: list[Memory] = []
            for m in items:
                if any(k.embedding.shape == m.embedding.shape and float(np.dot(k.embedding, m.embedding)) > 0.95 for k in kept):
                    await ses.delete(m)
                    removed += 1
                else:
                    kept.append(m)
    return removed
