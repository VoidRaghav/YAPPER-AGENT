import asyncio
import base64
import logging
import random
import time
from collections import defaultdict
from collections.abc import Coroutine
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import prompts
from app.config import Settings
from app.db import BotLine, Group, Message, db
from app.embeddings import Embedder, make_embedder
from app.gateway_client import DryRunGateway, HttpGateway
from app.graph import build_graph
from app.llm import make_llm
from app.nodes import commands, memory, vision, voice
from app.nodes.gate import names_bot
from app.nodes.humanize import reply_delay
from app.packs import Pack, PackBook, effective_spice
from app.repo import ensure_group, ensure_person, group_by_jid
from app.schemas import Event, Sent
from app.scheduler import start_scheduler
from app.trends import pipeline
from app.utils import from_unix, utcnow

log = logging.getLogger(__name__)

POSITIVE = {"😂", "🤣", "💀", "😭", "🔥", "👏", "💯", "❤", "🫡", "😆", "😹", "🙌", "👍", "🤌"}
NEGATIVE = {"👎", "😐", "🙄", "🤦", "😑", "🥱", "🤡"}
MEDIA_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
BOT_PREFIX = {"voice": "[voice note] ", "meme": "[meme] ", "sticker": "[sticker] "}


class Runtime:
    def __init__(self, settings: Settings, llm: Any, gateway: Any, embedder: Embedder, rng: random.Random | None = None):
        self.s = settings
        self.llm = llm
        self.gateway = gateway
        self.embedder = embedder
        self.rng = rng or random.Random()
        self.allow_all = False
        self.immediate = False
        self.ingest_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.think_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.timers: dict[str, asyncio.Task] = {}
        self.first_pending: dict[str, float] = {}
        self.busy_until: dict[int, datetime] = {}
        self.extracting: set[int] = set()
        self.dry_intros: set[int] = set()
        self.packs = PackBook(settings)
        self.tasks: set[asyncio.Task] = set()
        self.scheduler = None
        self.graph = build_graph(self)

    @classmethod
    def build(cls, settings: Settings) -> "Runtime":
        gateway = DryRunGateway(settings) if settings.dry_run else HttpGateway(settings)
        return cls(settings, make_llm(settings), gateway, make_embedder(settings))

    async def start(self) -> None:
        if self.s.trends_enabled:
            self.spawn(pipeline.seed(self))
        self.scheduler = start_scheduler(self)

    async def stop(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
        for task in [*self.timers.values(), *self.tasks]:
            task.cancel()
        await self.gateway.close()

    async def drain(self) -> None:
        while self.tasks or self.timers:
            await asyncio.gather(*self.tasks, *self.timers.values(), return_exceptions=True)

    def allowed(self, jid: str) -> bool:
        return self.allow_all or jid in self.s.allowed_groups

    def busy(self, group_id: int, now: datetime) -> bool:
        until = self.busy_until.get(group_id)
        return bool(until and until > now)

    def spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed", exc_info=task.exception())

    def submit(self, ev: Event) -> None:
        self.spawn(self.handle(ev))

    async def handle(self, ev: Event) -> int | None:
        if not self.allowed(ev.group_jid):
            log.warning("event from non-allowlisted group %s ignored", ev.group_jid)
            return None
        if ev.type == "message":
            for attempt in range(3):
                try:
                    return await self.on_message(ev)
                except OperationalError as e:
                    if "locked" not in str(e) or attempt == 2:
                        raise
                    log.warning("database busy, retrying message %s", ev.id)
                    await asyncio.sleep(1 + attempt)
        if ev.type in ("bot_added", "group_update"):
            await self.on_group(ev, intro=ev.type == "bot_added")
        return None

    async def on_group(self, ev: Event, intro: bool) -> None:
        async with db.session() as ses:
            group = await ensure_group(ses, ev.group_jid, ev.group_name, self.s, from_unix(ev.ts))
            gid, needs_intro = group.id, intro and self.s.intro_on_add and not group.intro_sent
        if needs_intro:
            await self.intro(gid, ev.group_jid, from_unix(ev.ts))

    async def intro(self, group_id: int, jid: str, now: datetime) -> None:
        async with db.session() as ses:
            group = await ses.get(Group, group_id)
            if not self.gateway.dry_run:
                if group.intro_sent:
                    return
                group.intro_sent = True
            pack = self.pack_of(group)
        if self.gateway.dry_run:
            if group_id in self.dry_intros:
                return
            self.dry_intros.add(group_id)
        await self.say(group_id, jid, self.intro_text(pack, jid), now=now, kind="intro")
        self.busy_until.pop(group_id, None)

    def intro_text(self, pack: Pack, jid: str) -> str:
        s = self.s
        bot, tag = s.bot_name, s.shared_bot_prefix.strip()
        owner = s.owner_name or ("is number ke owner" if pack.hindi_like else "the owner")
        shared = s.shared_number and jid.endswith("@g.us")
        if pack.hindi_like:
            disclosure = "main AI hoon aur group ke saare messages padhta hoon."
            note = f" {owner} ke number se chalta hoon, toh {tag} wale messages mere, baaki {owner} ke. bulana ho toh '{bot.lower()}' likho" if shared else ""
            control = f"on/off ka remote {owner} ke paas hai. " if s.owner_known else "pasand na aaye toh !off. "
            optout = "meri yaadein mitani ho toh !forget me, baaki sab !help mein"
        else:
            disclosure = "I'm an AI and I read every message in this group."
            note = f" I run on {owner}'s number, so my messages start with {tag}. Say '{bot.lower()}' to call me" if shared else ""
            control = f"Only {owner} can switch me on or off. " if s.owner_known else "Type !off if you'd rather I leave. "
            optout = "Type !forget me to wipe what I remember about you, or !help for more"
        return prompts.render(
            "intro", PACK_INTRO=pack.intro.replace("{BOT_NAME}", bot), DISCLOSURE=disclosure, SHARED_NOTE=note, CONTROL_NOTE=control, OPTOUT=optout
        )

    def pack_of(self, group: Group) -> Pack:
        return self.packs.get(group.pack)

    def spice_of(self, group: Group) -> int:
        return effective_spice(self.pack_of(group), group.spice)

    def can_unlabelled(self, jid: str) -> bool:
        return not (self.s.shared_number and jid.endswith("@g.us"))

    def _save_media(self, group_id: int, key: str, mimetype: str, data: bytes) -> str:
        folder = self.s.media_dir / str(group_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{key}{MEDIA_EXT.get(mimetype.split(';')[0], '.bin')}"
        path.write_bytes(data)
        return str(path)

    async def on_message(self, ev: Event) -> int | None:
        if ev.kind == "reaction":
            await self.on_reaction(ev)
            return None
        now = from_unix(ev.ts)
        cmd = commands.parse(ev.text)
        media = base64.b64decode(ev.media.b64) if ev.media and ev.media.b64 else None

        async with self.ingest_locks[ev.group_jid]:
            async with db.session() as ses:
                if ev.id and await ses.scalar(select(Message.id).where(Message.wa_id == ev.id).limit(1)):
                    return None
                group = await ensure_group(ses, ev.group_jid, ev.group_name, self.s, now)
                if not group.enabled and not cmd:
                    return None
                person = await ensure_person(ses, group, ev.sender_jid, ev.sender_alt_jid, ev.sender_name, now) if ev.sender_jid else None
                msg = Message(
                    group_id=group.id,
                    person_id=person.id if person else None,
                    wa_id=ev.id,
                    ts=now,
                    type=ev.kind,
                    text=(ev.text or "")[:4000],
                    reply_to_wa_id=ev.quoted.id if ev.quoted else None,
                    mentioned_bot=ev.mentioned_bot,
                    raw_json={
                        "seconds": ev.media.seconds if ev.media else None,
                        "filename": ev.media.filename if ev.media else None,
                        "mentions": ev.mentions,
                        "quoted_text": ev.quoted.text if ev.quoted else None,
                        "quoted_participant": ev.quoted.participant if ev.quoted else None,
                    },
                )
                if media and ev.kind == "image":
                    msg.media_path = self._save_media(group.id, ev.id or uuid4().hex, ev.media.mimetype, media)
                ses.add(msg)
                group.msgs_since_mood += 1
                group.msgs_since_memory += 1
                await ses.flush()
                gid, mid, pid = group.id, msg.id, person.id if person else None
                needs_intro = self.s.intro_on_add and not group.intro_sent
                memory_due = group.msgs_since_memory >= self.s.memory_every_n

        if media:
            note = None
            if ev.kind in ("voice", "audio"):
                note = await voice.transcribe(self, media)
            elif ev.kind == "image":
                note = await vision.describe(self, media)
            if note:
                async with db.session() as ses:
                    (await ses.get(Message, mid)).transcript = note

        if needs_intro:
            await self.intro(gid, ev.group_jid, now)
        if cmd:
            await commands.handle(self, ev, gid, mid, pid, cmd, now)
        elif not self.immediate:
            self.schedule(ev.group_jid, ev.mentioned_bot or names_bot(ev.text, self.s.aliases))
        if memory_due:
            if self.immediate:
                await memory.extract(self, gid)
            else:
                self.spawn(memory.extract(self, gid))
        return mid

    async def on_reaction(self, ev: Event) -> None:
        if not ev.reaction or not ev.reaction.emoji:
            return
        emoji = ev.reaction.emoji.replace("️", "")
        delta = 1 if emoji in POSITIVE else -1 if emoji in NEGATIVE else 0
        if not delta:
            return
        async with db.session() as ses:
            line = await ses.scalar(select(BotLine).where(BotLine.wa_id == ev.reaction.target_id).limit(1))
            if line:
                line.reaction_score += delta

    def schedule(self, jid: str, urgent: bool) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        first = self.first_pending.setdefault(jid, now)
        delay = self.s.gate_mention_debounce_seconds if urgent else self.s.gate_debounce_seconds
        delay = max(0.0, min(delay, self.s.gate_max_wait_seconds - (now - first)))
        old = self.timers.pop(jid, None)
        if old:
            old.cancel()
        self.timers[jid] = asyncio.create_task(self._fire(jid, delay))

    async def _fire(self, jid: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self.timers.pop(jid, None)
        self.first_pending.pop(jid, None)
        self.spawn(self.think(jid))

    async def think(self, jid: str, now: datetime | None = None) -> dict:
        async with db.session() as ses:
            group = await group_by_jid(ses, jid)
        if not group:
            return {}
        return await self.run_turn(group.id, now or utcnow())

    async def run_turn(self, group_id: int, now: datetime, forced: dict | None = None) -> dict:
        async with self.think_locks[group_id]:
            try:
                result = await self.graph.ainvoke({"group_id": group_id, "now": now, "forced": forced, "started": time.monotonic()})
            except Exception:
                log.exception("turn failed for group %s", group_id)
                return {}
        d = result.get("decision", {})
        log.info("turn group=%s action=%s mode=%s reason=%s outcome=%s", group_id, d.get("action"), d.get("mode"), d.get("reason"), result.get("outcome", "-"))
        return result

    async def dispatch(
        self,
        group_id: int,
        jid: str,
        job: dict,
        *,
        now: datetime,
        text: str,
        kind: str,
        mode: str = "",
        trend_ref: str = "",
        target_wa_id: str | None = None,
    ) -> bool:
        ref = uuid4().hex[:20]
        job = {**job, "jid": jid, "ref": ref}
        delay = job.get("delay_ms", 0) / 1000
        async with db.session() as ses:
            ses.add(
                BotLine(
                    group_id=group_id, ts=now, text=text, kind=kind, mode=mode, trend_ref=trend_ref,
                    target_wa_id=target_wa_id, ref=ref, status="queued", dry_run=self.gateway.dry_run,
                )
            )
            if kind != "reaction":
                ses.add(
                    Message(
                        group_id=group_id, ts=now + timedelta(seconds=delay), type="text",
                        text=BOT_PREFIX.get(kind, "") + text, from_bot=True, raw_json={"ref": ref, "kind": kind},
                    )
                )
        self.busy_until[group_id] = now + timedelta(seconds=delay + 2)
        ok = await self.gateway.send(job)
        if not ok or self.gateway.dry_run:
            await self._settle(ref, ok=ok, wa_id=None, status="dry" if ok else "failed")
            if not ok:
                self.busy_until.pop(group_id, None)
        return ok

    async def _settle(self, ref: str, *, ok: bool, wa_id: str | None, status: str) -> None:
        async with db.session() as ses:
            line = await ses.scalar(select(BotLine).where(BotLine.ref == ref).limit(1))
            if not line:
                return
            line.status, line.wa_id = status, wa_id or line.wa_id
            recent = (await ses.scalars(select(Message).where(Message.group_id == line.group_id, Message.from_bot.is_(True)).order_by(Message.id.desc()).limit(40))).all()
            for m in recent:
                if (m.raw_json or {}).get("ref") == ref:
                    if ok:
                        m.wa_id = wa_id or m.wa_id
                    else:
                        await ses.delete(m)

    async def on_sent(self, sent: Sent) -> None:
        if sent.ref:
            await self._settle(sent.ref, ok=sent.ok, wa_id=sent.wa_id, status="sent" if sent.ok else "failed")

    async def say(
        self, group_id: int, jid: str, text: str, *, now: datetime, kind: str = "command", quote_id: str | None = None, quote_participant: str | None = None
    ) -> bool:
        quick = kind == "command"
        delay = self.rng.uniform(1.5, 4.5) if quick else reply_delay(text, self.s, self.rng)
        job: dict = {"kind": "text", "text": text, "delay_ms": int(delay * 1000)}
        if quote_id:
            job["quote"] = {"id": quote_id, "participant": quote_participant}
        return await self.dispatch(group_id, jid, job, now=now, text=text, kind=kind)

    async def send_media(self, group_id: int, jid: str, data: bytes, media_kind: str, *, now: datetime, caption: str = "") -> bool:
        job = {"kind": media_kind, "media_b64": base64.b64encode(data).decode(), "text": caption, "delay_ms": int(self.rng.uniform(3, 8) * 1000)}
        return await self.dispatch(group_id, jid, job, now=now, text=caption or f"<{media_kind}>", kind="sticker" if media_kind == "sticker" else "meme")
