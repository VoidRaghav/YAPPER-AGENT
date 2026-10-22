import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db import Group, Memory, Message, Person, Trend, db
from app.nodes import awards, meme
from app.nodes.memory import forget_person
from app.repo import count_lines, find_person, is_protected, resolve_person
from app.safety import check_line
from app.schemas import Event
from app.utils import clip, to_local, week_bounds
from app.vectors import nearest

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

COMMAND = re.compile(r"^\s*(?:@\S+\s+)?!([a-z]+)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
ALWAYS = {"on", "help", "forget", "remember", "offlimits", "whatdoyouknow"}
KNOWN = ALWAYS | {"off", "quiet", "chatty", "roast", "stats", "meme", "sticker", "trends", "vibe", "spice"}

HELP = [
    ("quiet", "!quiet [minutes] - main chup"),
    ("chatty", "!chatty low/med/high - kitna bolun"),
    ("off", "!off / !on - band / chalu"),
    ("vibe", "!vibe / !vibe <pack> - packs dekho ya badlo (friends, family, study, work, community)"),
    ("spice", "!spice 0-5 - roast level"),
    ("roast", "!roast @name - order pe roast"),
    ("meme", "!meme <text> - meme banata hoon (image pe reply karke bhi)"),
    ("sticker", "!sticker - image ya message pe reply karo, sticker bana dunga"),
    ("stats", "!stats - is hafte ke stats"),
    ("whatdoyouknow", "!whatdoyouknow me - maine tere baare mein kya yaad rakha hai"),
    ("forget", "!forget me / !forget <text> - apni yaadein mitao"),
    ("remember", "!remember me - phir se yaad rakhna shuru"),
    ("offlimits", "!offlimits <topic> / !offlimits me / !offlimits list - tere liye no-joke topics"),
    ("trends", "!trends - aajkal kya chal raha hai"),
]
DENIALS = [
    "ye button sirf {owner} ke paas hai 😌",
    "nice try. remote {owner} ke haath mein hai",
    "pehle {owner} se permission lao, phir baat karte hain",
]


@dataclass(frozen=True)
class Command:
    name: str
    args: str


def _user(jid: str | None) -> str:
    user = (jid or "").split("@")[0]
    if user.startswith(("tg:", "dc:")):
        user = user[3:]
    return "".join(ch for ch in user.split(":")[0] if ch.isdigit())


def is_owner(s, ev: Event) -> bool:
    if not s.owner_known or ev.from_owner:
        return True
    return bool(s.owner_number_set & ({_user(ev.sender_jid), _user(ev.sender_alt_jid)} - {""}))


def owner_label(s) -> str:
    return s.owner_name or "owner"


def help_text(s) -> str:
    restricted = s.owner_only_set if s.owner_known else set()
    lines = [f"{text} (sirf {owner_label(s)})" if key in restricted or (key == "off" and "on" in restricted) else text for key, text in HELP]
    if s.owner_known:
        lines.append(f"!offlimits group <topic> - group ke liye no-joke topic (sirf {owner_label(s)})")
    return "commands:\n" + "\n".join(lines)


def parse(text: str) -> Command | None:
    m = COMMAND.match(text or "")
    if not m or m.group(1).lower() not in KNOWN:
        return None
    return Command(m.group(1).lower(), m.group(2).strip())


async def _target(ses, group_id: int, ev: Event, args: str) -> Person | None:
    for jid in ev.mentions:
        person = await find_person(ses, group_id, jid)
        if person:
            return person
    if ev.quoted and ev.quoted.participant and not ev.quoted.from_bot and not args:
        person = await find_person(ses, group_id, ev.quoted.participant)
        if person:
            return person
    name = re.sub(r"@\S+", "", args).strip() or args.strip()
    return await resolve_person(ses, group_id, name.split()[0]) if name else None


async def handle(rt: "Runtime", ev: Event, group_id: int, msg_id: int, sender_id: int | None, cmd: Command, now: datetime) -> None:
    s = rt.s
    reply: str | None = None
    forced: dict | None = None
    sticker_bytes: bytes | None = None

    owner = is_owner(s, ev)
    denied = not owner and cmd.name in s.owner_only_set and not (cmd.name == "vibe" and not cmd.args.strip())
    deny = rt.rng.choice(DENIALS).format(owner=owner_label(s))

    async with db.session() as ses:
        group = await ses.get(Group, group_id)
        sender = await ses.get(Person, sender_id) if sender_id else None
        if not group.enabled and (cmd.name not in ALWAYS or denied):
            return
        name = cmd.name
        args = cmd.args
        low = args.lower()

        if denied:
            log.info("command !%s from non-owner ignored in group %s", name, group_id)
            reply = deny
        elif name == "help":
            reply = help_text(s)
        elif name == "quiet":
            minutes = max(1, min(int(re.sub(r"\D", "", args) or 60), 24 * 60))
            group.quiet_until = now + timedelta(minutes=minutes)
            reply = rt.rng.choice([f"ok ok, {minutes} min ka maun vrat", f"theek hai, {minutes} min chup. miss mat karna", f"muting myself for {minutes} min. bye"])
        elif name == "chatty":
            level = {"low": "low", "med": "med", "medium": "med", "high": "high"}.get(low.split()[0] if low else "")
            if level:
                group.chattiness = level
                reply = {"low": "theek hai, kam bolunga", "med": "normal mode on", "high": "ab dekho meri bakchodi"}[level]
            else:
                reply = f"abhi {group.chattiness} pe hoon. options: low, med, high"
        elif name == "off":
            group.enabled = False
            reply = "theek hai, main chala. !on bolna jab yaad aaye"
        elif name == "on":
            group.enabled, group.quiet_until = True, None
            reply = "wapas aa gaya. kya miss kiya maine"
        elif name == "forget":
            if not sender:
                return
            if low in ("me", "mujhe", "everything about me"):
                count = await forget_person(ses, sender)
                reply = f"done. {count} cheezein mita di. ab se {sender.display_name} ke baare mein kuch yaad nahi rakhunga. !remember me se wapas on"
            elif args:
                scope = [Memory.group_id == group_id] + ([] if owner else [Memory.person_id == sender.id])
                rows = (await ses.scalars(select(Memory).where(*scope, Memory.text.ilike(f"%{args}%")).limit(3))).all()
                if not rows and rt.embedder.enabled:
                    vec = await rt.embedder.one(args)
                    rows = [m for m, sc in await nearest(ses, Memory, vec, scope, 2, min_score=0.6)]
                for m in rows:
                    await ses.delete(m)
                reply = ("bhool gaya: " + "; ".join(clip(m.text, 60) for m in rows)) if rows else "aisa kuch yaad hi nahi tha mujhe"
            else:
                reply = "kya bhoolun? !forget me ya !forget <text>"
        elif name == "remember":
            if sender and low in ("me", "mujhe"):
                sender.profile_json = {k: v for k, v in (sender.profile_json or {}).items() if k != "opt_out"}
                reply = "ok, ab se phir yaad rakhunga"
        elif name == "offlimits":
            reply = deny if not owner and low.startswith("group ") else _offlimits(group, sender, args, low, owner)
        elif name == "vibe":
            reply = _vibe(rt, group, low)
        elif name == "spice":
            digits = re.sub(r"\D", "", args)
            pack = rt.pack_of(group)
            if digits:
                group.spice = max(0, min(int(digits), 5))
            level = rt.spice_of(group)
            capped = "" if pack.roasts or level < 2 else f" ({pack.name} pack mein max 2)"
            reply = f"spice level: {level}/5{capped}"
        elif name == "roast" and rt.spice_of(group) < 3:
            reply = "is group mein roast mode band hai" if rt.pack_of(group).hindi_like else "roast mode is off in this group"
        elif name == "meme" and not rt.pack_of(group).features.memes:
            reply = "is group mein memes band hain" if rt.pack_of(group).hindi_like else "memes are off in this group"
        elif name == "roast":
            target = await _target(ses, group_id, ev, args)
            if not target:
                reply = "kisko? naam toh bata"
            elif is_protected(target, now):
                reply = f"{target.display_name} abhi protected hai. no roasts today"
            else:
                forced = {"action": "REPLY", "mode": "roast", "target_person_id": target.id, "target_id": msg_id,
                          "topic": f"{sender.display_name if sender else 'someone'} asked you to roast {target.display_name}. keep it affectionate."}
        elif name == "stats":
            start, end, _ = week_bounds(now, s.timezone)
            reply = awards.format_stats(await awards.compute(ses, s, group_id, start, end))
        elif name == "whatdoyouknow":
            target = sender if low in ("", "me") else await _target(ses, group_id, ev, args)
            if target and not owner and sender and target.id != sender.id:
                reply = "dusron ki file sirf unke paas hai. apni dekhni ho toh !whatdoyouknow me"
            else:
                reply = await _dossier(ses, target) if target else "kiske baare mein? @mention karo"
        elif name == "meme":
            image = await _quoted_media(ses, group_id, ev)
            forced = {"action": "MEME", "mode": "banter", "target_id": msg_id, "topic": args or "make a meme about what's happening in the chat", "image_path": image}
        elif name == "sticker":
            sticker_bytes, reply = await _sticker(ses, rt, group_id, ev, args, sender, now)
        elif name == "trends":
            rows = (await ses.scalars(select(Trend).where(Trend.safe.is_(True), Trend.evergreen.is_(False), Trend.expires_at > now).order_by(Trend.first_seen.desc()).limit(8))).all()
            reply = ("aajkal ye chal raha hai: " + ", ".join(t.name for t in rows)) if rows else "abhi fresh trends load nahi hue, classics se kaam chala raha hoon"

    if sticker_bytes:
        await rt.send_media(group_id, ev.group_jid, sticker_bytes, "sticker", now=now)
    if reply:
        async with db.session() as ses:
            recent = await count_lines(ses, now - timedelta(minutes=10), group_id, ("command",))
        if recent < s.command_reply_cap:
            await rt.say(group_id, ev.group_jid, reply, now=now, kind="command", quote_id=ev.id, quote_participant=ev.sender_jid)
        else:
            log.info("command %s applied silently in group %s (reply cap)", cmd.name, group_id)
    if forced:
        await rt.run_turn(group_id, now, forced)


def _vibe(rt: "Runtime", group: Group, low: str) -> str:
    packs = rt.packs.packs
    current = rt.pack_of(group)
    wanted = low.split()[0] if low.strip() else ""
    if not wanted:
        names = ", ".join(f"{p.id}{' (current)' if p.id == current.id else ''}" for p in packs.values())
        return f"packs: {names}"
    pack = packs.get(wanted)
    if not pack:
        return f"'{wanted}' naam ka koi pack nahi. options: {', '.join(packs)}"
    group.pack, group.spice = pack.id, None
    group.chattiness, group.language_mix = pack.chattiness_level, pack.language
    return f"vibe switched: {pack.name}. {pack.tagline}"


def _offlimits(group: Group, sender: Person | None, args: str, low: str, owner: bool = True) -> str:
    if low in ("", "list"):
        mine = ", ".join(sender.offlimits) if sender and sender.offlimits else "kuch nahi"
        return f"group off-limits: {', '.join(group.offlimits) or 'kuch nahi'}. tere off-limits: {mine}"
    if low in ("me", "mujhe"):
        if sender:
            sender.roastable = False
            return f"{sender.display_name} ab protected species hai. no roasts, promise"
        return "pehle kuch bolo toh sahi"
    if low.startswith("group "):
        topic = args[6:].strip().lower()
        if topic and topic not in group.offlimits:
            group.offlimits = [*group.offlimits, topic]
        return f"noted. '{topic}' pe group mein koi joke nahi"
    if low.startswith("remove "):
        topic = args[7:].strip().lower()
        if owner:
            group.offlimits = [t for t in group.offlimits if t != topic]
        if sender:
            sender.offlimits = [t for t in sender.offlimits if t != topic]
        return f"'{topic}' wapas allowed"
    if not sender:
        return "pehle kuch bolo toh sahi"
    topic = low.strip()
    if topic not in sender.offlimits:
        sender.offlimits = [*sender.offlimits, topic]
    return f"noted. {sender.display_name} ke '{topic}' pe koi joke nahi"


async def _dossier(ses, person: Person) -> str:
    rows = (await ses.scalars(select(Memory).where(Memory.person_id == person.id).order_by(Memory.id.desc()).limit(15))).all()
    profile = person.profile_json or {}
    lines = [f"jo mujhe {person.display_name} ke baare mein pata hai:"]
    if profile.get("opt_out"):
        lines.append("kuch nahi. inhone !forget me kiya hua hai")
        return "\n".join(lines)
    if profile.get("summary"):
        lines.append(f"profile: {profile['summary']}")
    if person.nicknames:
        lines.append(f"nicknames: {', '.join(person.nicknames)}")
    if person.offlimits or not person.roastable:
        lines.append(f"off-limits: {', '.join(person.offlimits) or ''}{' (pura banda protected hai)' if not person.roastable else ''}".strip())
    lines += [f"{'(sensitive, never joked about) ' if m.sensitive else ''}{m.text}" for m in rows]
    if len(lines) == 1:
        lines.append("abhi kuch khaas nahi. mysterious banda")
    lines.append("!forget me se sab mit jayega")
    return "\n".join(lines)


async def _quoted_media(ses, group_id: int, ev: Event) -> str | None:
    if not ev.quoted:
        return None
    return await ses.scalar(select(Message.media_path).where(Message.group_id == group_id, Message.wa_id == ev.quoted.id, Message.media_path.is_not(None)).limit(1))


async def _sticker(ses, rt: "Runtime", group_id: int, ev: Event, args: str, sender: Person | None, now: datetime) -> tuple[bytes | None, str | None]:
    image = await _quoted_media(ses, group_id, ev)
    if image:
        data = await meme.sticker_from_file(image)
        return (data, None) if data else (None, "ye image sticker nahi ban paayi")
    text, author = args, sender.display_name if sender else ""
    if ev.quoted and ev.quoted.text:
        text = ev.quoted.text
        quoted_person = await find_person(ses, group_id, ev.quoted.participant)
        author = rt.s.bot_name if ev.quoted.from_bot else (quoted_person.display_name if quoted_person else "someone")
    if not text:
        return None, "kis cheez ka sticker? image ya message pe reply karke !sticker bolo"
    verdict = check_line(clip(text, 200), mode="banter", max_chars=400, max_lines=6)
    if not verdict.ok and verdict.reason not in ("repeat", "markdown"):
        return None, "is wale ka sticker nahi banaunga, sorry"
    year = to_local(now, rt.s.timezone).year
    img = meme.build(rt.s, "quote", [clip(text, 200), f"- {author.lower()}, {year}"])
    return meme.to_sticker(img), None

