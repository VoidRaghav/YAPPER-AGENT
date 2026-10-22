import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.db import Group, db
from app.nodes import awards, memory
from app.trends import pipeline
from app.utils import utcnow

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)


async def trends_job(rt: "Runtime", with_scout: bool) -> None:
    try:
        await pipeline.refresh(rt, with_scout)
    except Exception:
        log.exception("trend refresh failed")


async def memory_sweep(rt: "Runtime") -> None:
    cutoff = utcnow() - timedelta(hours=6)
    async with db.session() as ses:
        groups = (await ses.scalars(select(Group).where(Group.msgs_since_memory > 0))).all()
        due = [g.id for g in groups if g.memory_at is None or g.memory_at < cutoff]
    for gid in due:
        await memory.extract(rt, gid)


def _cleanup_media(root: Path, days: int) -> int:
    if not root.exists():
        return 0
    cutoff = utcnow().timestamp() - days * 86400
    removed = 0
    for f in root.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def _backup_sqlite(url: str, folder: Path, keep: int) -> Path | None:
    if not url.startswith("sqlite") or ":memory:" in url:
        return None
    src = Path(url.split("///", 1)[1])
    if not src.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"yapper-{utcnow():%Y%m%d}.db"
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)
    for old in sorted(folder.glob("yapper-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return dst


async def nightly(rt: "Runtime") -> None:
    s = rt.s
    async with db.session() as ses:
        ids = (await ses.scalars(select(Group.id))).all()
    removed = 0
    for gid in ids:
        removed += await memory.consolidate(rt, gid)
    media = await asyncio.to_thread(_cleanup_media, s.media_dir, s.media_retention_days)
    backup = await asyncio.to_thread(_backup_sqlite, s.async_db_url, s.data_dir / "backups", s.backup_keep)
    log.info("nightly: %d duplicate memories merged, %d media files cleaned, backup=%s", removed, media, backup)


def start_scheduler(rt: "Runtime") -> AsyncIOScheduler:
    s = rt.s
    sched = AsyncIOScheduler(timezone=s.timezone)
    sched.add_job(
        awards.run_all,
        CronTrigger(day_of_week=s.awards_day, hour=s.awards_hour, minute=s.awards_minute, timezone=s.timezone),
        args=[rt], id="awards", coalesce=True, misfire_grace_time=3600,
    )
    if s.trends_enabled:
        sched.add_job(trends_job, IntervalTrigger(hours=s.trends_refresh_hours), args=[rt, False], id="trends", coalesce=True, next_run_time=_soon(1800))
        if s.trends_scout_enabled:
            sched.add_job(trends_job, IntervalTrigger(hours=s.trends_scout_hours), args=[rt, True], id="scout", coalesce=True, next_run_time=_soon(600))
    sched.add_job(memory_sweep, IntervalTrigger(minutes=30), args=[rt], id="memory", coalesce=True)
    sched.add_job(nightly, CronTrigger(hour=4, minute=20, timezone=s.timezone), args=[rt], id="nightly", coalesce=True)
    sched.start()
    log.info("scheduler started (%d jobs)", len(sched.get_jobs()))
    return sched


def _soon(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
