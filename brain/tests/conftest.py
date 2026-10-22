import itertools
import random
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.db import db
from app.embeddings import Embedder
from app.gateway_client import DryRunGateway
from app.runtime import Runtime
from app.schemas import Event, Media, Quoted
from app.utils import to_unix, utcnow

GROUP = "g1@g.us"
PEOPLE = {
    "aman": "911111111111@s.whatsapp.net",
    "priya": "912222222222@s.whatsapp.net",
    "rahul": "913333333333@s.whatsapp.net",
    "dev": "914444444444@s.whatsapp.net",
}


class FakeLLM:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.calls: list[tuple[str, str]] = []

    def on(self, schema_name: str, value: Any) -> None:
        self.handlers[schema_name] = value

    async def parse(self, schema: type[BaseModel], *, prompt: str, **_: Any):
        self.calls.append((schema.__name__, prompt))
        handler = self.handlers.get(schema.__name__)
        if handler is None:
            return None
        value = handler(prompt) if callable(handler) else handler
        return value if isinstance(value, schema) else schema.model_validate(value)

    async def research(self, **_: Any) -> str:
        return ""

    def called(self, schema_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == schema_name)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        db_url="sqlite+aiosqlite:///:memory:",
        dry_run=True,
        anthropic_api_key="test",
        embed_provider="none",
        transcribe=False,
        image_understanding=False,
        trends_enabled=False,
        intro_on_add=False,
        allowed_group_jids=GROUP,
        data_dir=tmp_path,
        warmup_hours=0,
        gate_sample_med=1.0,
        mention_voice_chance=0,
        memory_every_n=1000,
        min_gap_seconds=0,
        tts_provider="none",
    )


@pytest.fixture
async def database(settings):
    db.setup(settings.async_db_url)
    await db.create_all()
    yield db
    await db.dispose()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def rt(settings, database, llm):
    runtime = Runtime(settings, llm, DryRunGateway(settings), Embedder(), random.Random(7))
    runtime.allow_all = True
    runtime.immediate = True
    yield runtime
    await runtime.stop()


class Chat:
    def __init__(self, rt: Runtime, start: datetime | None = None) -> None:
        self.rt = rt
        self.clock = start or utcnow() - timedelta(hours=1)
        self.ids = itertools.count(1)

    @property
    def now(self) -> datetime:
        return self.clock

    def tick(self, seconds: float = 20) -> datetime:
        self.clock += timedelta(seconds=seconds)
        return self.clock

    async def say(self, who: str, text: str, *, gap: float = 20, mention: bool = False, quoted: Quoted | None = None, mentions: list[str] | None = None, kind: str = "text", media: Media | None = None, from_owner: bool = False) -> int | None:
        self.tick(gap)
        ev = Event(
            type="message",
            group_jid=GROUP,
            group_name="the boys",
            id=f"WA{next(self.ids)}{uuid4().hex[:6]}",
            sender_jid=PEOPLE.get(who, f"{who}@s.whatsapp.net"),
            sender_name=who.title(),
            ts=to_unix(self.clock),
            kind=kind,
            text=text,
            mentions=mentions or [],
            mentioned_bot=mention,
            from_owner=from_owner,
            quoted=quoted,
            media=media,
        )
        return await self.rt.handle(ev)

    async def think(self) -> dict:
        return await self.rt.think(GROUP, now=self.clock)


@pytest.fixture
def chat(rt) -> Chat:
    return Chat(rt)


def sent_texts(rt: Runtime, kind: str | None = None) -> list[str]:
    return [j.get("text") or j.get("emoji") or "" for j in rt.gateway.sent if kind is None or j["kind"] == kind]


def candidates(*texts: str, ref: str = "") -> Callable[[str], dict]:
    return lambda _prompt: {"candidates": [{"text": t, "ref": ref, "angle": "test"} for t in texts]}


def critic_pick(best: int = 0, score: int = 8, unsafe: tuple[int, ...] = ()) -> Callable[[str], dict]:
    def handler(prompt: str) -> dict:
        count = sum(1 for line in prompt.split("Candidates:")[-1].splitlines() if line[:1].isdigit())
        return {"reviews": [{"index": i, "safe": i not in unsafe, "score": score, "note": "x"} for i in range(count)], "best": best}

    return handler
