import logging
import random
from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.db import db
from app.embeddings import make_embedder
from app.gateway_client import DryRunGateway
from app.llm import make_llm
from app.runtime import Runtime


class NullLLM:
    async def parse(self, *_: Any, **__: Any) -> None:
        return None

    async def research(self, **_: Any) -> str:
        return ""


def setup_logging(level: str = "WARNING") -> None:
    logging.basicConfig(level=level.upper(), format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "anthropic", "openai", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def offline_runtime(settings: Settings, echo: Callable[[dict], None] | None = None, llm: Any = None) -> Runtime:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.setup(settings.async_db_url)
    await db.create_all()
    rt = Runtime(settings, llm or make_llm(settings), DryRunGateway(settings, echo), make_embedder(settings), random.Random())
    rt.allow_all = True
    rt.immediate = True
    return rt
