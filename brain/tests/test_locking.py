import asyncio
import random
import time
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import BotLine, Group, db
from app.embeddings import Embedder
from app.gateway_client import DryRunGateway
from app.runtime import Runtime
from tests.conftest import GROUP, Chat, FakeLLM, candidates, critic_pick, sent_texts


@pytest.fixture
async def file_rt(tmp_path):
    s = Settings(
        _env_file=None, db_url=f"sqlite+aiosqlite:///{tmp_path / 'y.db'}", dry_run=True, anthropic_api_key="t",
        embed_provider="none", transcribe=False, image_understanding=False, trends_enabled=False, intro_on_add=False,
        data_dir=tmp_path, warmup_hours=0, gate_sample_med=1.0, mention_voice_chance=0, memory_every_n=1000, min_gap_seconds=0, tts_provider="none",
    )
    db.setup(s.async_db_url)
    await db.create_all()
    llm = FakeLLM()
    rt = Runtime(s, llm, DryRunGateway(s), Embedder(), random.Random(3))
    rt.allow_all, rt.immediate = True, True
    yield rt, llm
    await rt.stop()
    await db.dispose()


def slow(llm: FakeLLM, schema_name: str, seconds: float) -> None:
    inner = llm.parse

    async def parse(schema, **kwargs):
        if schema.__name__ == schema_name:
            await asyncio.sleep(seconds)
        return await inner(schema, **kwargs)

    llm.parse = parse


@pytest.mark.parametrize("schema", ["GateOut", "MoodOut"])
async def test_new_messages_are_saved_while_a_model_is_thinking(file_rt, schema):
    rt, llm = file_rt
    llm.on("GateOut", {"action": "SKIP", "confidence": 0.1, "target": "", "emoji": "", "mode": "banter", "reason": "x"})
    llm.on("MoodOut", {"mood": "playful", "distressed_person": "", "reason": "x"})
    slow(llm, schema, 1.5)
    chat = Chat(rt)
    await chat.say("aman", "first message")
    turn = asyncio.create_task(chat.think())
    await asyncio.sleep(0.3)
    started = time.monotonic()
    await chat.say("priya", "second message while the model thinks")
    assert time.monotonic() - started < 0.5
    assert not turn.done()
    await turn


async def test_command_spam_is_capped_but_still_applied(chat, rt, settings):
    for i in range(8):
        await chat.say("aman", "!off" if i % 2 == 0 else "!on", gap=3)
    acks = [j for j in rt.gateway.sent if j["kind"] == "text"]
    assert len(acks) == settings.command_reply_cap
    async with db.session() as ses:
        assert (await ses.scalar(select(Group))).enabled is True


async def test_command_replies_do_not_use_the_conversation_budget(chat, rt, llm, settings):
    llm.on("Candidates", candidates("haan bolo"))
    llm.on("CriticOut", critic_pick())
    await chat.say("aman", "hi")
    async with db.session() as ses:
        group = await ses.scalar(select(Group))
        for i in range(settings.hourly_cap_per_group + 5):
            ses.add(BotLine(group_id=group.id, ts=chat.now - timedelta(minutes=30, seconds=i), text="ack", kind="command", status="sent"))
    await chat.say("priya", "yapper sun na", gap=60)
    result = await chat.think()
    assert result["decision"]["reason"] == "addressed"
    assert sent_texts(rt)[-1] == "haan bolo"


async def test_global_cap_still_counts_everything(chat, rt, settings):
    await chat.say("aman", "hi")
    async with db.session() as ses:
        group = await ses.scalar(select(Group))
        for i in range(settings.hourly_cap_global):
            ses.add(BotLine(group_id=group.id, ts=chat.now - timedelta(minutes=10, seconds=i), text="ack", kind="command", status="sent"))
    await chat.say("priya", "yapper sun na", gap=60)
    assert (await rt.think(GROUP, now=chat.now))["decision"]["reason"] == "hourly cap"
