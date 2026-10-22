import argparse
import asyncio
import base64
import itertools
import mimetypes
import re
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import Group, db
from app.nodes import awards, memory
from app.schemas import Event, Media
from app.trends import pipeline
from app.utils import slug, to_unix, utcnow
from scripts.common import offline_runtime, setup_logging

JID = "simulate@g.us"
LINE = re.compile(r"^\s*([^:/]{1,30}):\s*(.+)$")
HELP = """type messages as  name: text   (e.g.  aman: bro 5 min mein aaya)
mention the bot with @yapper (or its name). commands like !roast @aman work too.
/voice name path.ogg   send a voice note       /image name path.jpg [caption]   send an image
/memory   run the memory extractor now          /awards   post this week's awards
/trends   refresh the meme bank from the web    /mood     show current mood
/quit"""


def echo_factory(out_dir: Path):
    counter = itertools.count(1)

    def echo(job: dict) -> None:
        kind = job["kind"]
        delay = job.get("delay_ms", 0) / 1000
        if kind == "text":
            print(f"\n  \033[1;36m{get_settings().bot_name.lower()}\033[0m ({delay:.0f}s typing): {job['text']}\n")
        elif kind == "reaction":
            print(f"\n  \033[1;36m{get_settings().bot_name.lower()}\033[0m reacted {job['emoji']}\n")
        else:
            ext = {"voice": ".ogg", "sticker": ".webp", "image": ".jpg"}[kind]
            path = out_dir / f"{next(counter):03d}-{kind}{ext}"
            path.write_bytes(base64.b64decode(job["media_b64"]))
            print(f"\n  \033[1;36m{get_settings().bot_name.lower()}\033[0m sent a {kind}: {path}\n")

    return echo


async def main(args: argparse.Namespace) -> None:
    setup_logging(args.log)
    base = get_settings()
    db_path = base.data_dir / ("simulate.db" if not args.fresh else "simulate-fresh.db")
    if args.fresh and db_path.exists():
        db_path.unlink()
    s = base.model_copy(update={"db_url": f"sqlite:///{db_path}", "dry_run": True, "intro_on_add": False, "warmup_hours": 0, "chattiness_default": args.chatty})
    out_dir = s.data_dir / "simulate_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    rt = await offline_runtime(s, echo=echo_factory(out_dir))
    if s.trends_enabled:
        await pipeline.seed(rt)
    if s.missing_keys():
        print(f"warning: set {', '.join(s.missing_keys())} in .env for LLM_PROVIDER={s.llm_provider}, otherwise those jobs stay silent")
    print(f"provider: {s.llm_provider}  writer: {s.writer_model}  critic: {s.critic_model}  fast: {s.gate_model}  backup: {s.backup_model or '-'}")
    print(HELP)
    ids = itertools.count(1)
    speaker = "you"

    async def send(who: str, text: str, kind: str = "text", media: Media | None = None) -> None:
        await rt.handle(
            Event(type="message", group_jid=JID, group_name=args.group, id=f"S{next(ids)}-{utcnow().timestamp():.0f}",
                  sender_jid=f"{slug(who)}@sim", sender_name=who, ts=to_unix(utcnow()), kind=kind, text=text, media=media)
        )
        result = await rt.think(JID)
        if args.verbose and result:
            d = result.get("decision", {})
            print(f"  (mood={result.get('mood')} action={d.get('action')} reason={d.get('reason')})")

    while True:
        try:
            raw = (await asyncio.to_thread(input, "> ")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            continue
        if raw in ("/quit", "/exit"):
            break
        if raw == "/help":
            print(HELP)
            continue
        async with db.session() as ses:
            group = await ses.scalar(select(Group).where(Group.wa_jid == JID))
        if raw == "/mood":
            print(f"  mood: {group.mood if group else 'unknown'}")
            continue
        if raw == "/memory" and group:
            print(f"  stored {await memory.extract(rt, group.id)} memories")
            continue
        if raw == "/awards" and group:
            await awards.recap_group(rt, group.id)
            continue
        if raw == "/trends":
            print(f"  added {await pipeline.refresh(rt, with_scout=True)} trend cards")
            continue
        if raw.startswith(("/voice ", "/image ")):
            parts = raw.split(maxsplit=3)
            if len(parts) < 3 or not Path(parts[2]).is_file():
                print("  usage: /voice name path   or   /image name path [caption]")
                continue
            data = base64.b64encode(Path(parts[2]).read_bytes()).decode()
            kind = "voice" if parts[0] == "/voice" else "image"
            mime = mimetypes.guess_type(parts[2])[0] or ("audio/ogg" if kind == "voice" else "image/jpeg")
            await send(parts[1], parts[3] if len(parts) > 3 else "", kind, Media(mimetype=mime, b64=data))
            continue
        m = LINE.match(raw)
        if m:
            speaker, text = m.group(1).strip(), m.group(2).strip()
        else:
            text = raw
        await send(speaker, text)

    await rt.drain()
    await rt.stop()
    await db.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Chat with Yapper in your terminal (DRY_RUN, no WhatsApp needed).")
    p.add_argument("--group", default="the boys")
    p.add_argument("--chatty", default="high", choices=["low", "med", "high"])
    p.add_argument("--fresh", action="store_true", help="start with an empty memory")
    p.add_argument("--verbose", "-v", action="store_true", help="show gate decisions")
    p.add_argument("--log", default="WARNING")
    asyncio.run(main(p.parse_args()))
