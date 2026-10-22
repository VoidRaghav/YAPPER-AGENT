import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import func, select

from app.config import get_settings
from app.db import Group, Trend, db
from app.runtime import Runtime
from app.schemas import Event, Sent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    logs = s.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=s.log_level.upper(), format=fmt)
    to_file = RotatingFileHandler(logs / "brain.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    to_file.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(to_file)
    for noisy in ("httpx", "httpx2", "apscheduler.executors.default", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    db.setup(s.async_db_url)
    await db.create_all()
    rt = Runtime.build(s)
    await rt.start()
    app.state.rt = rt
    logging.getLogger("yapper").info(
        "brain up: provider=%s dry_run=%s db=%s allowed_groups=%d writer=%s gate=%s", s.llm_provider, s.dry_run, db.dialect, len(s.allowed_groups), s.writer_model, s.gate_model
    )
    if s.missing_keys():
        logging.getLogger("yapper").warning("missing API keys for LLM_PROVIDER=%s: %s", s.llm_provider, ", ".join(s.missing_keys()))
    yield
    await rt.stop()
    await db.dispose()


app = FastAPI(title="Yapper brain", lifespan=lifespan)


def authorized(x_internal_token: str | None = Header(default=None)) -> None:
    token = get_settings().internal_token
    if token and x_internal_token != token:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/event", status_code=202, dependencies=[Depends(authorized)])
async def event(ev: Event, request: Request) -> dict:
    request.app.state.rt.submit(ev)
    return {"accepted": True}


@app.post("/sent", status_code=202, dependencies=[Depends(authorized)])
async def sent(body: Sent, request: Request) -> dict:
    request.app.state.rt.spawn(request.app.state.rt.on_sent(body))
    return {"accepted": True}


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    async with db.session() as ses:
        groups = await ses.scalar(select(func.count(Group.id)))
        trends = await ses.scalar(select(func.count(Trend.id)).where(Trend.safe.is_(True)))
    return {"ok": True, "dry_run": s.dry_run, "db": db.dialect, "groups": groups, "meme_bank": trends, "allowed_groups": len(s.allowed_groups)}
