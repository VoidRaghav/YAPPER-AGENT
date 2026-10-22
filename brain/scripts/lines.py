import argparse
import asyncio
import csv
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import BlockedLine, BotLine, Group, db
from app.utils import utcnow

FIELDS = ["id", "ts", "group", "kind", "mode", "trend_ref", "text", "reaction_score", "rating"]


async def export(path: str, days: int) -> None:
    since = utcnow() - timedelta(days=days)
    async with db.session() as ses:
        names = {g.id: g.name for g in (await ses.scalars(select(Group))).all()}
        rows = (await ses.scalars(select(BotLine).where(BotLine.ts >= since, BotLine.kind.in_(("text", "voice", "meme", "sticker"))).order_by(BotLine.ts))).all()
        blocked = (await ses.scalars(select(BlockedLine).where(BlockedLine.ts >= since).order_by(BlockedLine.ts))).all()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({"id": r.id, "ts": r.ts.isoformat(timespec="seconds"), "group": names.get(r.group_id, ""), "kind": r.kind, "mode": r.mode,
                        "trend_ref": r.trend_ref, "text": r.text, "reaction_score": r.reaction_score, "rating": "" if r.rating is None else r.rating})
    print(f"wrote {len(rows)} lines to {path}. fill the rating column with 1 (funny) or -1 (not funny), then run: python -m scripts.lines import {path}")
    if blocked:
        print(f"\n{len(blocked)} lines were blocked by the safety filter this period, e.g.:")
        for b in blocked[-8:]:
            print(f"  [{b.reason}] {b.text}")


async def import_ratings(path: str) -> None:
    count = 0
    with open(path, encoding="utf-8") as f:
        ratings = {int(r["id"]): int(r["rating"]) for r in csv.DictReader(f) if r.get("rating", "").strip() in ("1", "-1", "0")}
    async with db.session() as ses:
        for line_id, rating in ratings.items():
            line = await ses.get(BotLine, line_id)
            if line:
                line.rating = rating
                count += 1
    print(f"imported {count} ratings")


async def fewshot(n: int) -> None:
    async with db.session() as ses:
        rows = (await ses.scalars(select(BotLine).where(BotLine.kind == "text", BotLine.mode != "support"))).all()
    scored = sorted(rows, key=lambda r: (r.rating or 0) * 3 + r.reaction_score, reverse=True)
    best, worst = scored[:n], [r for r in scored[::-1] if (r.rating or 0) < 0 or r.reaction_score < 0][:n]
    print("best lines (paste the good ones into prompts/persona.md under the examples):")
    for r in best:
        print(f"  {r.text}")
    print("\nlines that flopped (add patterns to avoid to prompts/critic.md):")
    for r in worst:
        print(f"  {r.text}")


async def main(args: argparse.Namespace) -> None:
    db.setup(get_settings().async_db_url)
    await db.create_all()
    if args.cmd == "export":
        await export(args.path, args.days)
    elif args.cmd == "import":
        await import_ratings(args.path)
    else:
        await fewshot(args.n)
    await db.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Weekly funny review: export bot lines, rate them, feed the best back into the prompts.")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("path", nargs="?", default="bot_lines.csv")
    e.add_argument("--days", type=int, default=7)
    i = sub.add_parser("import")
    i.add_argument("path")
    f = sub.add_parser("fewshot")
    f.add_argument("-n", type=int, default=10)
    asyncio.run(main(p.parse_args()))
