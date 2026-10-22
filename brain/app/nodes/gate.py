import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from app import prompts
from app.db import Group, Message, db
from app.nodes.humanize import count_emoji
from app.nodes.mood import SERIOUS_MOODS
from app.packs import bullets
from app.repo import REPLY_KINDS, UNSOLICITED_KINDS, count_lines, last_line, people_map, recent_messages, transcript
from app.utils import local_midnight_utc

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

Action = Literal["SKIP", "REACT", "REPLY", "VOICE", "MEME"]
Mode = Literal["banter", "roast", "hype", "support", "comeback", "answer"]
SUPPORT_EMOJI = {"❤️", "🫂", "🙏", "💪", "❤"}
DEFAULT_REACT = "😂"
SERIOUS_DECLINES = [
    "abhi nahi yaar, mood thoda serious hai. baad mein pakka",
    "pass for now, chat thodi heavy hai. roast baad mein",
    "not the moment bhai. later",
]


class GateOut(BaseModel):
    action: Action
    confidence: float
    target: str
    emoji: str
    mode: Mode
    reason: str


def skip(reason: str) -> dict:
    return {"decision": {"action": "SKIP", "reason": reason}}


def names_bot(text: str, aliases: list[str]) -> bool:
    low = (text or "").lower()
    return any(re.search(r"(?<![a-z0-9])@?" + re.escape(a) + r"(?![a-z0-9])", low) for a in aliases if a)


def addressed(m: Message, aliases: list[str]) -> bool:
    return m.mentioned_bot or names_bot(m.text, aliases)


async def budget_left(ses, group_id: int, kind: str, cap: int, now, tz: str) -> bool:
    return cap > 0 and await count_lines(ses, local_midnight_utc(now, tz), group_id, (kind,)) < cap


def _target_id(raw: str, msgs: list[Message], fallback: Message) -> int:
    digits = re.sub(r"\D", "", raw or "")
    ids = {m.id for m in msgs}
    return int(digits) if digits and int(digits) in ids else fallback.id


async def gate_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    now = st["now"]
    forced = st.get("forced")
    mood = st.get("mood", "neutral")
    serious = mood in SERIOUS_MOODS

    async with db.session() as ses:
        group = await ses.get(Group, st["group_id"])
        msgs = await recent_messages(ses, group.id, s.gate_messages)
        cursor = group.think_cursor
        fresh = [m for m in msgs if m.id > cursor and not m.from_bot]
        if msgs:
            group.think_cursor = max(cursor, msgs[-1].id)

        if forced:
            if serious and forced.get("mode") != "support":
                return {
                    "decision": {"action": "ACK", "mode": "support", "reason": "serious mood"},
                    "chosen": {"text": rt.rng.choice(SERIOUS_DECLINES)},
                    "addressed": True,
                }
            return {"decision": {"confidence": 1.0, "reason": "command", **forced}, "addressed": True}
        if not fresh:
            return skip("nothing new")
        if now - fresh[-1].ts > timedelta(minutes=s.stale_minutes):
            return skip("stale")
        if not group.enabled:
            return skip("disabled")
        if group.quiet_until and group.quiet_until > now:
            return skip("quiet")
        if rt.busy(group.id, now):
            return skip("busy")

        hour_ago = now - timedelta(hours=1)
        if await count_lines(ses, hour_ago, group.id, REPLY_KINDS) >= s.hourly_cap_per_group or await count_lines(ses, hour_ago) >= s.hourly_cap_global:
            return skip("hourly cap")

        window = now - timedelta(minutes=s.cooldown_window_minutes)
        called = next((m for m in reversed(fresh) if addressed(m, s.aliases)), None)

        pack, spice, jid = rt.pack_of(group), rt.spice_of(group), group.wa_jid
        voice_ok = pack.features.voice and rt.can_unlabelled(jid)

        if called:
            if await count_lines(ses, window, group.id, REPLY_KINDS) >= s.mention_cap_per_window:
                return skip("mention cap")
            action = "REPLY"
            if voice_ok and not serious and rt.rng.random() < s.mention_voice_chance and await budget_left(ses, group.id, "voice", s.voice_daily_cap, now, s.timezone):
                action = "VOICE"
            return {
                "decision": {"action": action, "mode": "support" if serious else "answer", "confidence": 1.0, "reason": "addressed", "target_id": called.id},
                "addressed": True,
            }

        if await count_lines(ses, window, group.id, UNSOLICITED_KINDS) >= s.cooldown_max_messages:
            return skip("cooldown")
        last = await last_line(ses, group.id)
        if last and (now - last.ts).total_seconds() < s.min_gap_seconds:
            return skip("min gap")

        warm = now - group.created_at < timedelta(hours=s.warmup_hours)
        chattiness = "low" if warm else group.chattiness
        if rt.rng.random() > s.sample_rate(chattiness):
            return skip("sampled out")

        people = await people_map(ses, group.id)
        prompt = prompts.render(
            "gate_turn",
            MOOD=mood,
            CHATTINESS=chattiness,
            LAST_SPOKE=f"{int((now - last.ts).total_seconds() // 60)} min ago" if last else "never",
            TRANSCRIPT=transcript(msgs, people, s.bot_name, s.timezone, ids=True, new_after=cursor),
        )
        group_id, fallback_target = group.id, fresh[-1]

    out = await rt.llm.parse(
        GateOut,
        model=s.gate_model,
        system=prompts.render(
            "gate",
            BOT_NAME=s.bot_name,
            ROLE=pack.role,
            SPEAK_WHEN=bullets(pack.speak_when) or "- a genuinely good opening",
            QUIET_WHEN=bullets(pack.quiet_when) or "- logistics and busy back-and-forth",
            ROAST_MODES="roast (someone set themselves up), comeback (someone roasted the bot), " if spice >= 3 else "",
        ),
        prompt=prompt,
        effort=s.fast_effort,
        max_tokens=1500,
    )
    if out is None:
        return skip("gate unavailable")

    threshold = s.threshold(chattiness)
    action = out.action
    floor = threshold - 0.15 if action == "REACT" else threshold
    if action == "SKIP" or out.confidence < floor:
        return skip(f"llm: {out.reason}")

    mode = "banter" if spice < 3 and out.mode in ("roast", "comeback") else out.mode
    emoji = out.emoji.strip() if count_emoji(out.emoji.strip()) == 1 else DEFAULT_REACT
    if action == "REACT" and not (pack.features.reactions and rt.can_unlabelled(jid)):
        return skip("reactions off")
    if action == "VOICE" and not voice_ok:
        action = "REPLY"
    if action == "MEME" and not pack.features.memes:
        action = "REPLY"
    if serious:
        if action == "REACT" and emoji in SUPPORT_EMOJI:
            pass
        elif action != "REACT" and mode == "support" and out.confidence >= max(threshold, 0.85):
            action = "REPLY"
        else:
            return skip("serious mood")
        mode = "support"

    async with db.session() as ses:
        if action == "REACT" and not await budget_left(ses, group_id, "reaction", s.react_daily_cap, now, s.timezone):
            return skip("reaction budget")
        if action == "VOICE" and not await budget_left(ses, group_id, "voice", s.voice_daily_cap, now, s.timezone):
            action = "REPLY"
        if action == "MEME" and not await budget_left(ses, group_id, "meme", s.meme_daily_cap, now, s.timezone):
            action = "REPLY"

    return {
        "decision": {
            "action": action,
            "mode": mode,
            "confidence": out.confidence,
            "reason": out.reason,
            "emoji": emoji,
            "target_id": _target_id(out.target, msgs, fallback_target),
        },
        "addressed": False,
    }
