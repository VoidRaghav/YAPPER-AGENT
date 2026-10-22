from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import BlockedLine, BotLine, Group, Lore, Memory, Message, MoodLog, Person, WeeklyStat
from app.utils import clip, hhmm

UNSOLICITED_KINDS = ("text", "voice", "meme", "sticker", "reaction")
REPLY_KINDS = (*UNSOLICITED_KINDS, "intro", "awards")


async def group_by_jid(s: AsyncSession, jid: str) -> Group | None:
    return await s.scalar(select(Group).where(Group.wa_jid == jid))


async def ensure_group(s: AsyncSession, jid: str, name: str | None, settings: Settings, now: datetime) -> Group:
    group = await group_by_jid(s, jid)
    if group is None:
        group = Group(wa_jid=jid, name=name or "", language_mix=settings.language_mix, chattiness=settings.chattiness_default, created_at=now)
        s.add(group)
        await s.flush()
    elif name and group.name != name:
        group.name = name
    return group


async def find_person(s: AsyncSession, group_id: int, jid: str | None) -> Person | None:
    if not jid:
        return None
    return await s.scalar(
        select(Person).where(Person.group_id == group_id, or_(Person.wa_jid == jid, Person.alt_jid == jid)).limit(1)
    )


async def ensure_person(s: AsyncSession, group: Group, jid: str, alt: str | None, name: str | None, now: datetime) -> Person:
    person = await find_person(s, group.id, jid) or await find_person(s, group.id, alt)
    if person is None:
        person = Person(group_id=group.id, wa_jid=jid, alt_jid=alt, display_name=(name or jid.split("@")[0])[:120], first_seen=now, last_seen=now)
        s.add(person)
        await s.flush()
        return person
    if alt and not person.alt_jid and alt != person.wa_jid:
        person.alt_jid = alt
    if name and name != person.display_name:
        person.display_name = name[:120]
    person.last_seen = now
    return person


async def people_map(s: AsyncSession, group_id: int) -> dict[int, Person]:
    return {p.id: p for p in (await s.scalars(select(Person).where(Person.group_id == group_id))).all()}


def names_of(person: Person) -> list[str]:
    names = [person.display_name, *(person.nicknames or [])]
    first = person.display_name.split()[0] if person.display_name.split() else ""
    if first and first not in names:
        names.append(first)
    return [n for n in dict.fromkeys(n.strip() for n in names) if n]


def is_protected(person: Person, now: datetime) -> bool:
    return not person.roastable or (person.protected_until is not None and person.protected_until > now)


async def resolve_person(s: AsyncSession, group_id: int, query: str) -> Person | None:
    q = query.strip().lstrip("@").lower()
    if not q:
        return None
    people = (await s.scalars(select(Person).where(Person.group_id == group_id))).all()
    for p in people:
        if q in (p.wa_jid.split("@")[0], (p.alt_jid or "").split("@")[0]):
            return p
    for p in people:
        if q in (n.lower() for n in names_of(p)):
            return p
    for p in people:
        if any(n.lower().startswith(q) for n in names_of(p)):
            return p
    return None


async def recent_messages(s: AsyncSession, group_id: int, limit: int, upto_id: int | None = None) -> list[Message]:
    stmt = select(Message).where(Message.group_id == group_id, Message.type != "reaction")
    if upto_id:
        stmt = stmt.where(Message.id <= upto_id)
    rows = (await s.scalars(stmt.order_by(Message.id.desc()).limit(limit))).all()
    return list(reversed(rows))


async def count_lines(s: AsyncSession, since: datetime, group_id: int | None = None, kinds: tuple[str, ...] | None = None) -> int:
    stmt = select(func.count(BotLine.id)).where(BotLine.ts >= since, BotLine.status != "failed")
    if group_id is not None:
        stmt = stmt.where(BotLine.group_id == group_id)
    if kinds:
        stmt = stmt.where(BotLine.kind.in_(kinds))
    return int(await s.scalar(stmt) or 0)


async def last_line(s: AsyncSession, group_id: int, kinds: tuple[str, ...] = UNSOLICITED_KINDS) -> BotLine | None:
    return await s.scalar(
        select(BotLine).where(BotLine.group_id == group_id, BotLine.kind.in_(kinds), BotLine.status != "failed").order_by(BotLine.id.desc()).limit(1)
    )


async def recent_line_texts(s: AsyncSession, group_id: int, n: int) -> list[str]:
    rows = await s.scalars(
        select(BotLine.text).where(BotLine.group_id == group_id, BotLine.kind.in_(("text", "voice", "meme", "sticker"))).order_by(BotLine.id.desc()).limit(n)
    )
    return [t for t in rows.all() if t]


def render_message(m: Message) -> str:
    raw = m.raw_json or {}
    body = m.text or ""
    if m.type == "voice":
        secs = raw.get("seconds")
        head = f"[voice note{f' {secs}s' if secs else ''}]"
        body = f"{head} {m.transcript}" if m.transcript else head
    elif m.type == "image":
        desc = f"[image: {m.transcript}]" if m.transcript else "[image]"
        body = f"{desc} {m.text}".strip()
    elif m.type in ("sticker", "gif", "video", "location", "contact", "audio"):
        body = f"[{m.type}] {m.text}".strip()
    elif m.type == "document":
        body = f"[document {raw.get('filename') or ''}] {m.text}".strip()
    elif m.type == "poll":
        body = f"[poll] {m.text}"
    quoted = raw.get("quoted_text")
    if quoted:
        body = f'(replying to "{clip(quoted, 60)}") {body}'
    return clip(body, 400)


def speaker(m: Message, people: dict[int, Person], bot_name: str) -> str:
    if m.from_bot:
        return bot_name
    p = people.get(m.person_id or -1)
    return p.display_name if p else "someone"


def transcript(msgs: list[Message], people: dict[int, Person], bot_name: str, tz: str, *, ids: bool = False, new_after: int | None = None) -> str:
    lines = []
    for m in msgs:
        star = "* " if new_after is not None and m.id > new_after else ""
        tag = f"[m{m.id}] " if ids else ""
        lines.append(f"{star}{tag}{hhmm(m.ts, tz)} {speaker(m, people, bot_name)}: {render_message(m)}")
    return "\n".join(lines) or "(no messages yet)"


async def purge_group(s: AsyncSession, group_id: int) -> None:
    for model in (BotLine, BlockedLine, WeeklyStat, MoodLog, Memory, Lore, Message, Person):
        await s.execute(delete(model).where(model.group_id == group_id))
    await s.execute(delete(Group).where(Group.id == group_id))
