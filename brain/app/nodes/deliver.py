import base64
import logging
import time
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import Group, Lore, Message, Person, db
from app.nodes import meme, voice
from app.nodes.humanize import reply_delay

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

MIN_TYPING_SECONDS = 2.0


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def react_node(rt: "Runtime", st: dict) -> dict:
    d = st["decision"]
    async with db.session() as ses:
        group = await ses.get(Group, st["group_id"])
        target = await ses.get(Message, d.get("target_id")) if d.get("target_id") else None
        person = await ses.get(Person, target.person_id) if target and target.person_id else None
    if not target or not target.wa_id:
        return {"outcome": "no reaction target"}
    job = {
        "kind": "reaction",
        "emoji": d["emoji"],
        "quote": {"id": target.wa_id, "participant": person.wa_jid if person else None, "from_me": False},
        "delay_ms": int(reply_delay("", rt.s, rt.rng, "reaction") * 1000),
    }
    ok = await rt.dispatch(st["group_id"], group.wa_jid, job, now=st["now"], text=d["emoji"], kind="reaction", mode=d.get("mode", ""), target_wa_id=target.wa_id)
    return {"outcome": "reacted" if ok else "reaction failed"}


async def deliver_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    chosen = st.get("chosen")
    if not chosen:
        return {"outcome": "nothing to send"}
    d = st["decision"]
    ctx = st.get("context", {})
    now = st["now"]
    action = d["action"]
    text = chosen["text"]

    async with db.session() as ses:
        jid = (await ses.get(Group, st["group_id"])).wa_jid

    job: dict = {"kind": "text", "text": text}
    kind = "text"
    if action == "VOICE":
        audio = await voice.speak(rt, text)
        if audio:
            job, kind = {"kind": "voice", "media_b64": _b64(audio), "mimetype": "audio/ogg; codecs=opus"}, "voice"
    elif action == "MEME":
        plan = chosen.get("plan", {}) if rt.can_unlabelled(jid) else {**chosen.get("plan", {}), "sticker": False}
        rendered = await meme.render_plan(s, plan, ctx.get("image_path"))
        if rendered:
            data, media_kind = rendered
            job, kind = {"kind": media_kind, "media_b64": _b64(data)}, ("sticker" if media_kind == "sticker" else "meme")

    if ctx.get("target_wa_id") and job["kind"] != "sticker" and (st.get("addressed") or not ctx.get("target_is_last") or rt.rng.random() < s.quote_probability):
        job["quote"] = {"id": ctx["target_wa_id"], "participant": ctx.get("target_jid")}
    thinking = time.monotonic() - st.get("started", time.monotonic())
    job["delay_ms"] = int(max(MIN_TYPING_SECONDS, reply_delay(text, s, rt.rng, job["kind"]) - thinking) * 1000)

    ref = (chosen.get("ref") or "").strip().lower()
    trend_ref = next((n for n in ctx.get("trend_names", []) if n.lower() == ref), "")
    ok = await rt.dispatch(
        st["group_id"], jid, job, now=now, text=text, kind="command" if action == "ACK" else kind,
        mode=d.get("mode", ""), trend_ref=trend_ref, target_wa_id=ctx.get("target_wa_id"),
    )
    if ok and ref and ref in {t.lower() for t in ctx.get("lore_titles", [])}:
        async with db.session() as ses:
            lore = await ses.scalar(select(Lore).where(Lore.group_id == st["group_id"], Lore.title.ilike(ref)))
            if lore:
                lore.times_called_back += 1
                lore.last_called_back = now
    return {"outcome": f"{'sent' if ok else 'failed'} {kind}"}
