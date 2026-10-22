from datetime import UTC, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import BotLine, Group, db
from app.nodes import deliver
from tests.conftest import candidates, critic_pick, sent_texts


async def test_thinking_time_is_taken_out_of_the_typing_delay(chat, rt, llm, monkeypatch):
    llm.on("Candidates", candidates("haan bolo, kya hua"))
    llm.on("CriticOut", critic_pick())
    monkeypatch.setattr(deliver, "time", SimpleNamespace(monotonic=lambda: 10.0**9))
    await chat.say("aman", "@yapper", mention=True)
    await chat.think()
    assert rt.gateway.sent[-1]["delay_ms"] == int(deliver.MIN_TYPING_SECONDS * 1000)


async def _group():
    async with db.session() as ses:
        return await ses.scalar(select(Group))


async def _add_lines(n: int, at, kind: str = "text"):
    group = await _group()
    async with db.session() as ses:
        for i in range(n):
            ses.add(BotLine(group_id=group.id, ts=at - timedelta(seconds=i), text=f"line {i}", kind=kind, status="sent"))


async def test_mention_always_replies_without_gate_llm(chat, rt, llm):
    llm.on("Candidates", candidates("5 mins in aman time or actual time", "aman ka 5 min = 45 min", "IST matlab aman stretchable time"))
    llm.on("CriticOut", critic_pick(best=0))
    await chat.say("aman", "bro i'll reach in 5 mins")
    await chat.say("priya", "@yapper isko dekh", mention=True)
    result = await chat.think()
    assert result["decision"]["action"] == "REPLY"
    assert result["decision"]["reason"] == "addressed"
    assert llm.called("GateOut") == 0
    assert sent_texts(rt) == ["5 mins in aman time or actual time"]
    job = rt.gateway.sent[0]
    assert 3000 <= job["delay_ms"] <= 25000
    assert job["quote"]["id"]


async def test_bot_name_in_text_counts_as_mention(chat, rt, llm):
    llm.on("Candidates", candidates("present sir, as always unlike aman"))
    llm.on("CriticOut", critic_pick())
    await chat.say("rahul", "yapper tu kahan hai")
    assert (await chat.think())["decision"]["reason"] == "addressed"


async def test_silence_without_opening(chat, llm):
    llm.on("GateOut", {"action": "SKIP", "confidence": 0.1, "target": "", "emoji": "", "mode": "banter", "reason": "logistics"})
    await chat.say("aman", "kal 7 baje milte hai, address bhejo")
    result = await chat.think()
    assert result["decision"]["action"] == "SKIP"
    assert llm.called("Candidates") == 0


async def test_low_confidence_is_silence(chat, rt, llm):
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.5, "target": "", "emoji": "", "mode": "banter", "reason": "meh"})
    await chat.say("aman", "kuch bhi")
    await chat.think()
    assert rt.gateway.sent == []


async def test_cooldown_blocks_unsolicited(chat, rt, llm):
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.99, "target": "", "emoji": "", "mode": "banter", "reason": "x"})
    await chat.say("aman", "first")
    await _add_lines(3, chat.now)
    await chat.say("aman", "setup for a punchline")
    assert (await chat.think())["decision"]["reason"] == "cooldown"
    assert llm.called("GateOut") == 0


async def test_mentions_bypass_cooldown_but_not_mention_cap(chat, rt, llm, settings):
    llm.on("Candidates", candidates("haan bolo"))
    llm.on("CriticOut", critic_pick())
    await chat.say("aman", "first")
    await _add_lines(3, chat.now)
    await chat.say("aman", "@yapper oye", mention=True)
    assert (await chat.think())["decision"]["reason"] == "addressed"
    await _add_lines(settings.mention_cap_per_window, chat.now)
    await chat.say("aman", "@yapper oye again", mention=True, gap=30)
    assert (await chat.think())["decision"]["reason"] == "mention cap"


async def test_never_two_in_a_row_while_pending(chat, rt, llm):
    llm.on("Candidates", candidates("haan bolo"))
    llm.on("CriticOut", critic_pick())
    await chat.say("aman", "@yapper", mention=True)
    await chat.think()
    await chat.say("priya", "@yapper you too", mention=True, gap=1)
    assert (await chat.think())["decision"]["reason"] == "busy"


async def test_quiet_and_off(chat, rt, llm):
    await chat.say("aman", "hi")
    async with db.session() as ses:
        group = await ses.scalar(select(Group))
        group.quiet_until = chat.now + timedelta(minutes=30)
    await chat.say("aman", "@yapper", mention=True)
    assert (await chat.think())["decision"]["reason"] == "quiet"


async def test_warmup_forces_low_chattiness(chat, rt, llm, settings):
    settings.warmup_hours = 48
    settings.gate_sample_low = 0.0
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.99, "target": "", "emoji": "", "mode": "banter", "reason": "x"})
    await chat.say("aman", "something")
    assert (await chat.think())["decision"]["reason"] == "sampled out"


async def test_hourly_cap(chat, rt, llm, settings):
    await chat.say("aman", "hi")
    await _add_lines(settings.hourly_cap_per_group, chat.now - timedelta(minutes=20), kind="text")
    await chat.say("aman", "@yapper", mention=True)
    assert (await chat.think())["decision"]["reason"] == "hourly cap"


def _zone_far_from_midnight(now) -> str:
    return next(tz for tz in ("Asia/Kolkata", "Europe/London", "America/New_York", "Asia/Tokyo") if 2 <= now.replace(tzinfo=UTC).astimezone(ZoneInfo(tz)).hour <= 20)


async def test_react_path_and_daily_budget(chat, rt, llm, settings):
    settings.timezone = _zone_far_from_midnight(chat.now)
    mid = None
    llm.on("GateOut", lambda p: {"action": "REACT", "confidence": 0.9, "target": f"m{mid}", "emoji": "💀", "mode": "banter", "reason": "already funny"})
    mid = await chat.say("rahul", "i fell asleep in my own birthday party")
    await chat.think()
    assert rt.gateway.sent[-1]["kind"] == "reaction"
    assert rt.gateway.sent[-1]["emoji"] == "💀"
    settings.react_daily_cap = 1
    rt.busy_until.clear()
    mid = await chat.say("rahul", "again", gap=600)
    assert (await chat.think())["decision"]["reason"] == "reaction budget"


async def test_voice_and_meme_fall_back_to_reply_when_budget_spent(chat, rt, llm, settings):
    settings.voice_daily_cap = 0
    llm.on("GateOut", {"action": "VOICE", "confidence": 0.95, "target": "", "emoji": "", "mode": "hype", "reason": "big moment"})
    llm.on("Candidates", candidates("rank 1 aaya hai bhai, sharma ji ka beta retired today"))
    llm.on("CriticOut", critic_pick())
    await chat.say("dev", "guys i cracked the interview!!")
    result = await chat.think()
    assert result["decision"]["action"] == "REPLY"
    assert rt.gateway.sent[-1]["kind"] == "text"


async def test_critic_low_score_skips_unsolicited(chat, rt, llm):
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.95, "target": "", "emoji": "", "mode": "banter", "reason": "x"})
    llm.on("Candidates", candidates("generic line one", "generic line two"))
    llm.on("CriticOut", critic_pick(best=-1, score=3))
    await chat.say("aman", "anyone up")
    await chat.think()
    assert rt.gateway.sent == []


async def test_stale_backlog_after_downtime_is_ignored(chat, rt, llm):
    llm.on("Candidates", candidates("haan bolo"))
    await chat.say("aman", "@yapper you there?", mention=True)
    result = await rt.think("g1@g.us", now=chat.now + timedelta(minutes=45))
    assert result["decision"]["reason"] == "stale"
    assert rt.gateway.sent == []
