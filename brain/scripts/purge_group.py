import argparse
import asyncio
import shutil

from sqlalchemy import select

from app.config import get_settings
from app.db import Group, db
from app.repo import purge_group


async def main(args: argparse.Namespace) -> int:
    s = get_settings()
    db.setup(s.async_db_url)
    await db.create_all()
    async with db.session() as ses:
        groups = (await ses.scalars(select(Group))).all()
    if args.list or not args.jid:
        for g in groups:
            print(f"{g.wa_jid}  {g.name!r}  enabled={g.enabled}")
        await db.dispose()
        return 0
    group = next((g for g in groups if g.wa_jid == args.jid), None)
    if not group:
        print(f"no data for {args.jid}")
        await db.dispose()
        return 1
    if not args.yes and input(f"permanently delete everything stored for {group.name!r} ({group.wa_jid})? type yes: ").strip() != "yes":
        print("aborted")
        await db.dispose()
        return 1
    async with db.session() as ses:
        await purge_group(ses, group.id)
    shutil.rmtree(s.media_dir / str(group.id), ignore_errors=True)
    print(f"purged {group.wa_jid}: messages, people, memories, lore, bot lines, stats and media are gone")
    await db.dispose()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Delete everything Yapper stored for one group.")
    p.add_argument("jid", nargs="?", help="group jid, e.g. 1203630xxxxxxx@g.us")
    p.add_argument("--list", action="store_true", help="list stored groups")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    raise SystemExit(asyncio.run(main(p.parse_args())))
