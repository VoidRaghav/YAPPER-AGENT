import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import Trend, db
from app.trends import pipeline
from app.utils import utcnow
from scripts.common import offline_runtime, setup_logging


async def main(args: argparse.Namespace) -> None:
    setup_logging("INFO")
    s = get_settings()
    rt = await offline_runtime(s)
    await pipeline.seed(rt)
    added = await pipeline.refresh(rt, with_scout=args.scout)
    since = utcnow() - timedelta(days=2)
    async with db.session() as ses:
        fresh = (await ses.scalars(select(Trend).where(Trend.evergreen.is_(False), Trend.first_seen >= since).order_by(Trend.first_seen.desc()).limit(40))).all()
    print(f"\n{added} new cards. fresh in the last 2 days:")
    for t in fresh:
        print(f"  {'ok ' if t.safe else 'BLOCKED'} [{t.source}/{t.category}] {t.name}: {t.phrase} | use when: {t.usage}")
    await db.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Refresh the Indian meme bank now (Google Trends IN, Google News IN, Reddit, X if configured).")
    p.add_argument("--scout", action="store_true", help="also run the Claude web-search scout (costs a few cents)")
    asyncio.run(main(p.parse_args()))
