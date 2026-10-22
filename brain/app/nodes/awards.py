import json
import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import prompts
from app.config import Settings
from app.db import BlockedLine, BotLine, Group, Message, WeeklyStat, db
from app.nodes.humanize import clean
from app.nodes.roast import persona
from app.packs import platform_of, spice_rule
from app.repo import people_map
from app.safety import check_line
from app.utils import clip, to_local, utcnow, week_bounds

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

PLAN = re.compile(
    r"(?<![a-z])(?:plan\w*|chalo|chalein|chalte|let'?s go|kal milte|trip|goa|party|movie|dinner|lunch|meet ?up|milte hai\w*|kab mil\w*|weekend pe)(?![a-z])",
    re.IGNORECASE,
)
BREAK = re.compile(
    r"(?<![a-z])(?:cancel\w*|nahi aa (?:paunga|paungi|sakta|sakti)|nhi aa\w*|can'?t make it|cant make it|next time|sorry guys|skip kar\w*|busy hu|busy hoon|nahi ho payega|aaj nahi|rain check|i'?m out)(?![a-z])",
    re.IGNORECASE,
)


class Recap(BaseModel):
    text: str


def _winner(counter: Counter, names: dict[int, str]) -> tuple[int, str, float] | None:
    if not counter:
        return None
    pid, value = counter.most_common(1)[0]
    return (pid, names.get(pid, "someone"), value) if value else None


async def compute(ses: AsyncSession, s: Settings, group_id: int, start: datetime, end: datetime) -> dict:
    people = await people_map(ses, group_id)
    names = {pid: p.display_name for pid, p in people.items()}
    base = [Message.group_id == group_id, Message.from_bot.is_(False), Message.ts >= start, Message.ts < end, Message.person_id.is_not(None), Message.type != "reaction"]

    totals = (
        await ses.execute(
            select(Message.person_id, func.count(Message.id), func.coalesce(func.sum(func.length(Message.text)), 0))
            .where(*base)
            .group_by(Message.person_id)
            .order_by(func.count(Message.id).desc())
        )
    ).all()
    rows = (await ses.execute(select(Message.id, Message.person_id, Message.ts, Message.text).where(*base).order_by(Message.ts, Message.id))).all()

    stats: dict[str, dict] = {"total_messages": {"value": len(rows)}}
    if totals:
        pid, count, chars = totals[0]
        stats["biggest_yapper"] = {"person_id": pid, "person": names.get(pid), "value": count, "detail": {"chars": int(chars)}}

    ghost_gap = timedelta(hours=s.ghost_hours)
    ghosted: Counter = Counter()
    worst: tuple[float, int, int, str] | None = None
    reply_times: dict[int, list[float]] = defaultdict(list)
    night: Counter = Counter()
    plans: Counter = Counter()
    breaks: Counter = Counter()

    next_other: list[int | None] = [None] * len(rows)
    for i in range(len(rows) - 2, -1, -1):
        next_other[i] = i + 1 if rows[i + 1][1] != rows[i][1] else next_other[i + 1]

    for i, (mid, pid, ts, text) in enumerate(rows):
        nxt = next_other[i]
        gap = rows[nxt][2] - ts if nxt is not None else timedelta(0)
        if gap > ghost_gap:
            ghosted[pid] += 1
            if text and len(text) >= 12:
                weight = gap.total_seconds() * (1.5 if "?" in text else 1)
                if worst is None or weight > worst[0]:
                    worst = (weight, mid, pid, text)
        if i and rows[i - 1][1] != pid:
            delta = (ts - rows[i - 1][2]).total_seconds()
            if 0 <= delta < 1800:
                reply_times[pid].append(delta)
        if to_local(ts, s.timezone).hour < 5:
            night[pid] += 1
        if text and PLAN.search(text):
            plans[pid] += 1
        if text and BREAK.search(text):
            breaks[pid] += 1

    for key, counter in (("most_ghosted", ghosted), ("night_owl", night), ("plans_maker", plans), ("plans_breaker", breaks)):
        win = _winner(counter, names)
        if win:
            stats[key] = {"person_id": win[0], "person": win[1], "value": win[2]}
    if worst:
        _, mid, pid, text = worst
        stats["most_ignored_message"] = {"person_id": pid, "person": names.get(pid), "value": round(worst[0] / 3600, 1), "example_msg_id": mid, "detail": {"text": clip(text, 140)}}
    medians = {pid: statistics.median(v) for pid, v in reply_times.items() if len(v) >= 5}
    if medians:
        pid = min(medians, key=medians.get)
        stats["chronically_online"] = {"person_id": pid, "person": names.get(pid), "value": round(medians[pid])}

    sent = int(await ses.scalar(select(func.count(BotLine.id)).where(BotLine.group_id == group_id, BotLine.ts >= start, BotLine.kind.in_(("text", "voice", "meme", "sticker")))) or 0)
    chickened = int(await ses.scalar(select(func.count(BlockedLine.id)).where(BlockedLine.group_id == group_id, BlockedLine.ts >= start)) or 0)
    best = await ses.scalar(
        select(BotLine).where(BotLine.group_id == group_id, BotLine.ts >= start, BotLine.reaction_score > 0).order_by(BotLine.reaction_score.desc()).limit(1)
    )
    stats["bot"] = {"value": sent, "detail": {"jokes_it_chickened_out_of": chickened, "best_line": best.text if best else None, "best_line_reactions": best.reaction_score if best else 0}}
    return stats


async def save(ses: AsyncSession, group_id: int, week: str, stats: dict) -> None:
    await ses.execute(delete(WeeklyStat).where(WeeklyStat.group_id == group_id, WeeklyStat.week == week))
    for key, item in stats.items():
        ses.add(
            WeeklyStat(
                group_id=group_id,
                week=week,
                stat_key=key,
                person_id=item.get("person_id"),
                value=float(item.get("value") or 0),
                example_msg_id=item.get("example_msg_id"),
                detail=item.get("detail") or {},
            )
        )


def format_stats(stats: dict) -> str:
    lines = ["this week so far:"]
    get = stats.get
    if get("biggest_yapper"):
        lines.append(f"biggest yapper: {stats['biggest_yapper']['person']} ({int(stats['biggest_yapper']['value'])} msgs)")
    if get("most_ghosted"):
        lines.append(f"most ghosted: {stats['most_ghosted']['person']} ({int(stats['most_ghosted']['value'])} msgs left hanging)")
    if get("most_ignored_message"):
        m = stats["most_ignored_message"]
        lines.append(f'most ignored: {m["person"]}\'s "{m["detail"]["text"]}" ({m["value"]}h of silence)')
    if get("chronically_online"):
        lines.append(f"chronically online: {stats['chronically_online']['person']} (replies in ~{int(stats['chronically_online']['value'])}s)")
    if get("night_owl"):
        lines.append(f"night owl: {stats['night_owl']['person']} ({int(stats['night_owl']['value'])} msgs after midnight)")
    if get("plans_maker"):
        lines.append(f"plans maker: {stats['plans_maker']['person']} ({int(stats['plans_maker']['value'])} plans pitched)")
    if get("plans_breaker"):
        lines.append(f"plans breaker: {stats['plans_breaker']['person']} ({int(stats['plans_breaker']['value'])} bails)")
    if len(lines) == 1:
        lines.append("abhi tak kuch khaas nahi hua. boring week")
    return "\n".join(lines)


async def write_recap(rt: "Runtime", group: Group, stats: dict) -> str:
    s = rt.s
    pack = rt.pack_of(group)
    offlimits = list(dict.fromkeys([*(group.offlimits or []), *pack.off_limits]))
    ctx = {
        "pack_id": pack.id,
        "spice": rt.spice_of(group),
        "platform": platform_of(group.wa_jid),
        "GROUP_NAME": group.name or "the group",
        "language": group.language_mix or pack.language,
    }
    out = await rt.llm.parse(
        Recap,
        model=s.awards_model,
        system=persona(rt, ctx),
        prompt=prompts.render(
            "awards",
            RECAP_STYLE=pack.recap_style,
            SPICE_RULE=spice_rule(ctx["spice"]),
            STATS=json.dumps(stats, ensure_ascii=False, default=str, indent=1),
            GROUP_OFFLIMITS=", ".join(offlimits) or "(none)",
        ),
        effort=s.writer_effort,
        max_tokens=8000,
        creative=True,
    )
    fallback = format_stats(stats).replace("this week so far:", "weekly awards are here")
    if not out:
        return fallback
    lines = [clean(line, s.aliases, pack.vibe.formality <= 1, max(pack.vibe.emoji, 1)) for line in out.text.splitlines() if line.strip()][:8]
    for line in lines:
        verdict = check_line(line, mode="roast", offlimits=offlimits, max_chars=400, max_lines=1, allow_profanity=s.allow_profanity)
        if not verdict.ok and verdict.reason not in ("repeat",):
            log.info("awards line blocked (%s): %s", verdict.reason, line)
            return fallback
    return "\n".join(lines) or fallback


async def recap_group(rt: "Runtime", group_id: int, now: datetime | None = None) -> bool:
    s = rt.s
    now = now or utcnow()
    start, end, week = week_bounds(now, s.timezone)
    start = min(start, now - timedelta(days=7))
    async with db.session() as ses:
        group = await ses.get(Group, group_id)
        if not group.enabled or not rt.pack_of(group).features.weekly_recap:
            return False
        stats = await compute(ses, s, group_id, start, end)
        await save(ses, group_id, week, stats)
    if stats["total_messages"]["value"] < s.awards_min_messages:
        log.info("awards skipped for group %s: only %s messages", group_id, stats["total_messages"]["value"])
        return False
    text = await write_recap(rt, group, stats)
    return await rt.say(group_id, group.wa_jid, text, now=now, kind="awards")


async def run_all(rt: "Runtime") -> None:
    async with db.session() as ses:
        groups = (await ses.scalars(select(Group).where(Group.enabled.is_(True)))).all()
    for g in groups:
        if rt.allowed(g.wa_jid):
            try:
                await recap_group(rt, g.id)
            except Exception:
                log.exception("awards failed for group %s", g.id)
