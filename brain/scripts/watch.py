import argparse
import asyncio
import re
import shutil
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import BlockedLine, BotLine, Group, Message, Person, db
from app.utils import clip, to_local, utcnow

TURN = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO app\.runtime: turn group=(\d+) action=(\S+) mode=(\S+) reason=(.*) outcome=(.*)$")
PROBLEM = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (WARNING|ERROR) (\S+): (.*)$")
NOISE = {"nothing new", "busy"}
FINAL = {"sent": ("🤖 sent", "green"), "dry": ("🤖 dry-run", "cyan"), "failed": ("🤖 FAILED", "red")}
COLOR = {"dim": "2", "cyan": "36", "green": "32", "yellow": "33", "red": "31", "magenta": "35", "bold": "1"}


def paint(text: str, color: str, on: bool) -> str:
    return f"\033[{COLOR[color]}m{text}\033[0m" if on else text


class Watcher:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.s = get_settings()
        self.color = not args.no_color
        self.log_path = self.s.data_dir / "logs" / "brain.log"
        self.log_offset = 0
        self.last = {"msg": 0, "line": 0, "block": 0}
        self.pending: set[int] = set()
        self.groups: dict[int, str] = {}
        self.people: dict[int, str] = {}
        self.aliases = [a.lower() for a in self.s.aliases]
        self.width = max(60, shutil.get_terminal_size((120, 20)).columns - 46)

    def local(self, ts: datetime) -> datetime:
        return to_local(ts, self.s.timezone).replace(tzinfo=None)

    def wanted(self, group_id: int) -> bool:
        return not self.args.group or self.args.group.lower() in self.groups.get(group_id, "").lower()

    def row(self, when: datetime, group_id: int | None, who: str, text: str, color: str) -> tuple[datetime, str]:
        group = clip(self.groups.get(group_id, "brain") if group_id is not None else "brain", 18)
        head = f"{when:%H:%M:%S}  {group:<18}  {who:<14}"
        return when, f"{paint(head, 'dim', self.color)}  {paint(clip(text, self.width), color, self.color)}"

    async def poll_db(self, since: datetime | None) -> list[tuple[datetime, str]]:
        out: list[tuple[datetime, str]] = []
        async with db.session() as ses:
            self.groups = {g.id: g.name or g.wa_jid for g in (await ses.scalars(select(Group))).all()}

            q = select(Message).where(Message.id > self.last["msg"], Message.from_bot.is_(False))
            if since:
                q = q.where(Message.ts >= since)
            msgs = (await ses.scalars(q.order_by(Message.id))).all()
            missing = {m.person_id for m in msgs if m.person_id and m.person_id not in self.people}
            if missing:
                for p in (await ses.scalars(select(Person).where(Person.id.in_(missing)))).all():
                    self.people[p.id] = p.display_name
            for m in msgs:
                self.last["msg"] = max(self.last["msg"], m.id)
                if not self.wanted(m.group_id):
                    continue
                body = m.text or f"[{m.type}]"
                calls = m.mentioned_bot or any(a in body.lower() for a in self.aliases)
                color = "magenta" if body.startswith("!") else "yellow" if calls else "bold"
                out.append(self.row(self.local(m.ts), m.group_id, clip(self.people.get(m.person_id, "someone"), 14), body, color))

            q = select(BotLine).where(BotLine.id > self.last["line"])
            if since:
                q = q.where(BotLine.ts >= since)
            lines = list((await ses.scalars(q.order_by(BotLine.id))).all())
            for b in lines:
                self.last["line"] = max(self.last["line"], b.id)
            if self.pending:
                lines += (await ses.scalars(select(BotLine).where(BotLine.id.in_(self.pending)))).all()
            for b in lines:
                if b.status not in FINAL:
                    self.pending.add(b.id)
                    continue
                self.pending.discard(b.id)
                if not self.wanted(b.group_id):
                    continue
                label, color = FINAL[b.status]
                kind = "" if b.kind == "text" else f"[{b.kind}] "
                out.append(self.row(self.local(b.ts), b.group_id, label, kind + (b.text or ""), color))

            q = select(BlockedLine).where(BlockedLine.id > self.last["block"])
            if since:
                q = q.where(BlockedLine.ts >= since)
            for x in (await ses.scalars(q.order_by(BlockedLine.id))).all():
                self.last["block"] = max(self.last["block"], x.id)
                if self.wanted(x.group_id):
                    out.append(self.row(self.local(x.ts), x.group_id, "✋ blocked", f"{x.reason}: {x.text}", "red"))
        return out

    def poll_log(self, since: datetime | None) -> list[tuple[datetime, str]]:
        if not self.log_path.exists():
            return []
        size = self.log_path.stat().st_size
        if size < self.log_offset:
            self.log_offset = 0
        out: list[tuple[datetime, str]] = []
        with self.log_path.open(encoding="utf-8", errors="replace") as f:
            f.seek(self.log_offset)
            chunk = f.read()
            self.log_offset = f.tell()
        for line in chunk.splitlines():
            if m := TURN.match(line):
                when = datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
                gid, action, reason, outcome = int(m[2]), m[3], m[5], m[6]
                if (since and when < self.local(since)) or not self.wanted(gid):
                    continue
                if action == "SKIP" and reason in NOISE and not self.args.all:
                    continue
                color = "dim" if action == "SKIP" else "cyan"
                tail = f" -> {outcome}" if outcome not in ("-", "") else ""
                out.append(self.row(when, gid, "decision", f"{action} ({reason}){tail}", color))
            elif m := PROBLEM.match(line):
                when = datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
                if since and when < self.local(since):
                    continue
                color = "red" if m[2] == "ERROR" else "yellow"
                out.append(self.row(when, None, m[2].lower(), f"{m[3]}: {m[4]}", color))
        return out

    async def tick(self, since: datetime | None = None) -> None:
        events = await self.poll_db(since) + self.poll_log(since)
        for _, text in sorted(events, key=lambda e: e[0]):
            print(text, flush=True)

    async def run(self) -> None:
        since = utcnow() - timedelta(minutes=self.args.minutes)
        db.setup(self.s.async_db_url)
        legend = "incoming (yellow = calls yapper, purple = command) | decision | 🤖 sent | ✋ blocked | warnings"
        print(paint(f"watching {self.s.async_db_url.split('/')[-1]} + {self.log_path}", "dim", self.color))
        print(paint(legend, "dim", self.color))
        if not self.log_path.exists():
            print(paint("no brain.log yet: restart the brain once so decisions and warnings show up here", "yellow", self.color))
        try:
            await self.tick(since)
            while not self.args.once:
                await asyncio.sleep(self.args.interval)
                await self.tick()
        finally:
            await db.dispose()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Live feed of what Yapper sees, decides and sends.")
    p.add_argument("--group", default="", help="only show groups whose name contains this text")
    p.add_argument("--minutes", type=float, default=30, help="how far back to start (default 30)")
    p.add_argument("--all", action="store_true", help="also show routine skips (nothing new, busy)")
    p.add_argument("--interval", type=float, default=1.5, help="seconds between checks")
    p.add_argument("--once", action="store_true", help="print the recent history and exit")
    p.add_argument("--no-color", action="store_true")
    try:
        asyncio.run(Watcher(p.parse_args()).run())
    except KeyboardInterrupt:
        pass
