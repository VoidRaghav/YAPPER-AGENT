import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from app import prompts
from app.db import Group, MoodLog, Person, db
from app.llm import bounded
from app.repo import people_map, recent_messages, resolve_person, transcript
from app.safety import detect_serious

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

SERIOUS_MOODS = {"serious", "venting", "argument"}
Mood = Literal["playful", "hyped", "neutral", "serious", "venting", "argument", "logistics"]


class MoodOut(BaseModel):
    mood: Mood
    distressed_person: str
    reason: str


async def mood_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    now = st["now"]
    ttl = timedelta(minutes=s.mood_ttl_minutes)
    protect_until = now + timedelta(hours=s.serious_protect_hours)
    prompt = None
    async with db.session() as ses:
        group = await ses.get(Group, st["group_id"])
        msgs = await recent_messages(ses, group.id, s.mood_messages)
        fresh = [m for m in msgs if m.id > group.think_cursor and not m.from_bot]

        hits = [(m, detect_serious(m.content)) for m in fresh]
        hits = [(m, cue) for m, cue in hits if cue]
        if hits:
            for m, cue in hits:
                if m.person_id:
                    person = await ses.get(Person, m.person_id)
                    person.protected_until = protect_until
                log.info("serious cue %r in group %s", cue, group.id)
            group.mood, group.mood_at, group.msgs_since_mood = "serious", now, 0
            ses.add(MoodLog(group_id=group.id, ts=now, mood="serious", source="keyword"))
            return {"mood": "serious"}

        stale = group.mood_at is None or now - group.mood_at > ttl
        if fresh and (group.msgs_since_mood >= s.mood_every_n or stale or st.get("forced")):
            people = await people_map(ses, group.id)
            prompt = transcript(msgs, people, s.bot_name, s.timezone)
        current = group.mood if group.mood_at is not None and now - group.mood_at <= ttl else "neutral"

    if prompt is None:
        return {"mood": current}

    out = await bounded(rt.llm.parse(
        MoodOut,
        model=s.mood_model,
        system=prompts.render("mood", BOT_NAME=s.bot_name),
        prompt=prompt,
        effort=s.fast_effort,
        max_tokens=1500,
    ), s.mood_timeout_seconds)
    if out is None:
        return {"mood": current}

    async with db.session() as ses:
        group = await ses.get(Group, st["group_id"])
        group.mood, group.mood_at, group.msgs_since_mood = out.mood, now, 0
        ses.add(MoodLog(group_id=group.id, ts=now, mood=out.mood, source="llm"))
        if out.mood in SERIOUS_MOODS and out.distressed_person:
            person = await resolve_person(ses, group.id, out.distressed_person)
            if person:
                person.protected_until = protect_until
    return {"mood": out.mood}
