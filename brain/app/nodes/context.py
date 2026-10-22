from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Group, Lore, Memory, Message, Person, db
from app.nodes.mood import SERIOUS_MOODS
from app.repo import is_protected, names_of, people_map, recent_line_texts, recent_messages, render_message, speaker, transcript
from app.packs import platform_of
from app.trends import store as trend_store
from app.utils import clip, to_local
from app.vectors import keyword_rank, nearest

if TYPE_CHECKING:
    from app.runtime import Runtime


def describe_person(p: Person, now: datetime) -> str:
    bits = [p.display_name]
    if p.nicknames:
        bits.append("aka " + ", ".join(p.nicknames[:4]))
    summary = (p.profile_json or {}).get("summary")
    if summary:
        bits.append(clip(summary, 300))
    if p.offlimits:
        bits.append("off-limits: " + ", ".join(p.offlimits))
    if is_protected(p, now):
        bits.append("PROTECTED right now, do not roast or tease them")
    return "- " + " | ".join(bits)


def bullet(lines: list[str], empty: str = "(nothing yet)") -> str:
    return "\n".join(lines) if lines else empty


async def recall_memories(
    ses: AsyncSession, group_id: int, focus: set[int], qvec: np.ndarray | None, text: str, k: int, now: datetime
) -> list[Memory]:
    filters = [Memory.group_id == group_id, Memory.sensitive.is_(False)]
    hits = [m for m, _ in await nearest(ses, Memory, qvec, filters, k)]
    if not hits:
        pool = (await ses.scalars(select(Memory).where(*filters).order_by(Memory.id.desc()).limit(400))).all()
        hits = [m for m, _ in keyword_rank(text, list(pool), lambda m: m.text, k)]
    seen = {m.id for m in hits}
    for pid in focus:
        latest = (await ses.scalars(select(Memory).where(*filters, Memory.person_id == pid).order_by(Memory.id.desc()).limit(2))).all()
        hits.extend(m for m in latest if m.id not in seen and not seen.add(m.id))
    for m in hits:
        m.last_used_at = now
    return hits


async def recall_lore(ses: AsyncSession, group_id: int, qvec: np.ndarray | None, text: str, k: int) -> list[Lore]:
    hits = [lore for lore, _ in await nearest(ses, Lore, qvec, [Lore.group_id == group_id], k, min_score=0.2)]
    pool = (await ses.scalars(select(Lore).where(Lore.group_id == group_id).order_by(Lore.times_called_back.desc(), Lore.id.desc()).limit(200))).all()
    if not hits:
        hits = [lore for lore, _ in keyword_rank(text, list(pool), lambda x: f"{x.title} {x.description}", k)]
    for lore in pool[:2]:
        if lore not in hits:
            hits.append(lore)
    return hits


async def recall_episodes(ses: AsyncSession, group_id: int, qvec: np.ndarray | None, before: datetime, k: int) -> list[Message]:
    filters = [Message.group_id == group_id, Message.from_bot.is_(False), Message.ts < before]
    return [m for m, _ in await nearest(ses, Message, qvec, filters, k, scan_limit=6000, min_score=0.45)]


async def context_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    now = st["now"]
    d = st["decision"]
    forced = st.get("forced") or {}
    mood = st.get("mood", "neutral")
    mode = "support" if mood in SERIOUS_MOODS else d.get("mode", "banter")

    async with db.session() as ses:
        group = await ses.get(Group, st["group_id"])
        people = await people_map(ses, group.id)
        msgs = await recent_messages(ses, group.id, s.context_messages)
        target = next((m for m in msgs if m.id == d.get("target_id")), None)
        if target is None and d.get("target_id"):
            target = await ses.get(Message, d["target_id"])
        if target is None and msgs:
            target = next((m for m in reversed(msgs) if not m.from_bot), msgs[-1])

        focus = {m.person_id for m in msgs[-12:] if m.person_id}
        if target and target.person_id:
            focus.add(target.person_id)
        victim = people.get(forced.get("target_person_id") or -1)
        if victim:
            focus.add(victim.id)

        topic = forced.get("topic", "")
        focus_text = (" ".join(m.content for m in msgs[-6:] if m.content) + " " + topic).strip()
        qvec = await rt.embedder.one(focus_text[-2000:]) if focus_text else None

        memories = await recall_memories(ses, group.id, focus, qvec, focus_text, s.memory_recall_k, now)
        lore = await recall_lore(ses, group.id, qvec, focus_text, s.lore_k)
        episodes = await recall_episodes(ses, group.id, qvec, now - timedelta(hours=s.episodic_min_age_hours), s.episodic_k)
        pack, spice = rt.pack_of(group), rt.spice_of(group)
        use_trends = pack.features.trends and mode != "support"
        trends = await trend_store.pick(ses, s, group.id, qvec, focus_text, now, rt.rng) if use_trends else []
        recent = await recent_line_texts(ses, group.id, s.recent_lines_buffer)

        protected = [n for p in people.values() if is_protected(p, now) for n in names_of(p)]
        if victim and is_protected(victim, now):
            protected.extend(names_of(victim))
        offlimits = list(dict.fromkeys([*(group.offlimits or []), *pack.off_limits]))
        person_offlimits = [f"{people[pid].display_name}: {t}" for pid in focus if pid in people for t in people[pid].offlimits]
        offlimit_terms = offlimits + [t for pid in focus if pid in people for t in people[pid].offlimits]
        target_person = people.get(target.person_id) if target and target.person_id else None
        last_msg = msgs[-1] if msgs else None

        def who(pid: int | None) -> str:
            return people[pid].display_name if pid in people else "group"

        target_line = f"{speaker(target, people, s.bot_name)}: {render_message(target)}" if target else "(the chat in general)"
        if victim:
            target_line = f"{target_line}\nRoast request aimed at: {victim.display_name}"
        if topic:
            target_line = f"{target_line}\nTopic: {topic}"

        ctx = {
            "GROUP_NAME": group.name or "the group",
            "LANGUAGE_MIX": group.language_mix or s.language_mix,
            "MOOD": mood,
            "MODE": mode,
            "REASON": d.get("reason", ""),
            "PEOPLE": bullet([describe_person(people[pid], now) for pid in focus if pid in people]),
            "MEMORIES": bullet([f"- {who(m.person_id)}: {m.text} ({m.kind})" for m in memories]),
            "LORE": bullet([f"- {x.title}: {x.description}" for x in lore]),
            "EPISODES": bullet(
                [f"- [{to_local(m.ts, s.timezone):%d %b}] {speaker(m, people, s.bot_name)}: {clip(m.content, 160)}" for m in episodes]
            ),
            "TRENDS": trend_store.format_trends(trends, now) if use_trends else "(not used in this group)",
            "ROLE": pack.role,
            "OFFLIMITS": bullet([f"- {t}" for t in offlimits + person_offlimits], "(none beyond the hard rules)"),
            "PROTECTED": ", ".join(dict.fromkeys(protected)) or "(nobody)",
            "RECENT_LINES": bullet([f"- {t}" for t in recent[:15]], "(none)"),
            "TRANSCRIPT": transcript(msgs, people, s.bot_name, s.timezone),
            "TARGET": target_line,
            "offlimit_terms": offlimit_terms,
            "protected_names": list(dict.fromkeys(protected)),
            "recent_lines": recent,
            "trend_names": [t.name for t in trends],
            "lore_titles": [x.title for x in lore],
            "target_wa_id": target.wa_id if target else None,
            "target_jid": target_person.wa_jid if target_person else None,
            "target_is_last": bool(target and last_msg and target.id == last_msg.id),
            "group_jid": group.wa_jid,
            "pack_id": pack.id,
            "spice": spice,
            "language": group.language_mix or pack.language,
            "platform": platform_of(group.wa_jid),
            "image_path": forced.get("image_path"),
        }
    return {"context": ctx, "decision": {**d, "mode": mode}}
