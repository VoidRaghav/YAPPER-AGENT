import argparse
import asyncio
import csv
from collections import Counter
from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import db
from app.export_parser import load_export
from app.gateway_client import describe
from app.nodes.gate import names_bot
from app.schemas import Event
from app.trends import pipeline
from app.utils import clip, slug, to_unix
from scripts.common import NullLLM, offline_runtime, setup_logging

JID = "replay@g.us"


async def main(args: argparse.Namespace) -> None:
    setup_logging(args.log)
    base = get_settings()
    s = base.model_copy(
        update={
            "db_url": "sqlite+aiosqlite:///:memory:",
            "dry_run": True,
            "intro_on_add": False,
            "transcribe": False,
            "image_understanding": False,
            "trends_scout_enabled": False,
            "warmup_hours": 0 if args.skip_warmup else base.warmup_hours,
        }
    )
    sent: list[dict] = []
    rt = await offline_runtime(s, echo=sent.append, llm=NullLLM() if args.no_llm else None)
    if s.trends_enabled:
        await pipeline.seed(rt)

    tz = ZoneInfo(s.timezone)
    msgs = [m for m in load_export(args.file, not args.monthfirst) if m.sender.lower() not in s.aliases]
    msgs = msgs[-args.limit :] if args.limit else msgs
    print(f"replaying {len(msgs)} messages from {args.file} ({'rules only' if args.no_llm else 'with LLM'})")

    rows: list[dict] = []
    actions: Counter = Counter()
    reasons: Counter = Counter()
    for i, m in enumerate(msgs):
        utc = m.ts.replace(tzinfo=tz).astimezone(UTC).replace(tzinfo=None)
        await rt.handle(
            Event(
                type="message", group_jid=JID, group_name=args.group, id=f"R{i}", sender_jid=f"{slug(m.sender)}@replay",
                sender_name=m.sender, ts=to_unix(utc),
                kind=m.kind, text=m.text,
            )
        )
        urgent = names_bot(m.text, s.aliases)
        gap = (msgs[i + 1].ts - m.ts).total_seconds() if i + 1 < len(msgs) else 1e9
        if not (urgent or gap >= s.gate_debounce_seconds):
            continue
        before = len(sent)
        wait = s.gate_mention_debounce_seconds if urgent else s.gate_debounce_seconds
        result = await rt.think(JID, now=utc + timedelta(seconds=wait))
        d = result.get("decision", {})
        action, reason = d.get("action", "?"), d.get("reason", "")
        actions[action] += 1
        reasons["llm" if reason.startswith("llm:") else reason] += 1
        out = [describe(j) for j in sent[before:]]
        rows.append({"time": m.ts.isoformat(), "sender": m.sender, "text": m.text, "action": action, "mode": d.get("mode", ""), "reason": reason, "bot": " | ".join(out)})
        if out:
            print(f"\n{m.ts:%d %b %H:%M} {m.sender}: {clip(m.text, 90)}\n    -> [{action}/{d.get('mode', '')}] {' | '.join(out)}")

    await rt.drain()
    spoken = sum(1 for j in sent if j["kind"] != "reaction")
    reacts = sum(1 for j in sent if j["kind"] == "reaction")
    print("\n--- summary ---")
    print(f"human messages: {len(msgs)}  gate runs: {sum(actions.values())}")
    print(f"bot messages: {spoken} ({spoken / max(len(msgs), 1):.1%} of human messages)  reactions: {reacts}")
    print(f"actions: {dict(actions)}")
    print(f"skip reasons: {dict(reasons.most_common(10))}")
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["time"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} decisions to {args.out}")
    await rt.stop()
    await db.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Replay an exported WhatsApp chat through Yapper's gate offline (DRY_RUN, in-memory DB).")
    p.add_argument("file", help="WhatsApp 'Export chat' .txt file")
    p.add_argument("--group", default="replay group")
    p.add_argument("--limit", type=int, default=400, help="replay only the last N messages (0 = all)")
    p.add_argument("--monthfirst", action="store_true", help="export uses MM/DD dates")
    p.add_argument("--no-llm", action="store_true", help="rules only, no API calls")
    p.add_argument("--skip-warmup", action="store_true", help="ignore the 48h warm-up so you see normal chattiness")
    p.add_argument("--out", help="write every gate decision to this CSV")
    p.add_argument("--log", default="WARNING")
    asyncio.run(main(p.parse_args()))
