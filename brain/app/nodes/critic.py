import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from app import prompts
from app.db import BlockedLine, db
from app.llm import bounded
from app.nodes.humanize import clean
from app.packs import spice_rule
from app.safety import check_line

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

FALLBACKS = {
    "support": ["here for you yaar, seriously", "that's rough. we're all here", "sending a big hug. call kar agar baat karni ho"],
    "default": ["pass. agla sawal", "main is pe chup hi theek hoon", "no comments, meri legal team ne mana kiya hai", "hmm. sochna padega"],
}


class Review(BaseModel):
    index: int
    safe: bool
    score: int
    note: str


class CriticOut(BaseModel):
    reviews: list[Review]
    best: int


def fallback(rt: "Runtime", mode: str) -> dict:
    return {"text": rt.rng.choice(FALLBACKS["support" if mode == "support" else "default"]), "ref": "", "angle": "fallback"}


async def critic_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    ctx = st["context"]
    d = st["decision"]
    mode = d.get("mode", "banter")
    called = bool(st.get("addressed"))
    is_meme = d["action"] == "MEME"

    survivors: list[dict] = []
    blocked: list[tuple[str, str]] = []
    pack = rt.packs.get(ctx.get("pack_id"))
    casual, max_emoji = pack.vibe.formality <= 1, pack.vibe.emoji
    for c in st.get("candidates", []):
        text = clean(c["text"], s.aliases, casual, max_emoji)
        verdict = check_line(
            text,
            mode=mode,
            offlimits=ctx["offlimit_terms"],
            protected=ctx["protected_names"],
            recent=ctx["recent_lines"],
            max_chars=s.max_reply_chars * (2 if is_meme else 1),
            max_lines=s.max_reply_lines,
            repeat_threshold=s.repeat_similarity,
            allow_profanity=s.allow_profanity,
        )
        if verdict.ok:
            if is_meme:
                c = {**c, "plan": {**c["plan"], "captions": [clean(x, s.aliases, casual, max_emoji) for x in c["plan"]["captions"]]}}
            survivors.append({**c, "text": text})
        else:
            blocked.append((text, verdict.reason))

    if not survivors:
        await _log_blocked(st["group_id"], st["now"], blocked)
        return {"chosen": fallback(rt, mode) if called and not is_meme else None}

    out = await bounded(rt.llm.parse(
        CriticOut,
        model=s.critic_model,
        system=prompts.render("critic", BOT_NAME=s.bot_name, ROLE=pack.role, SPICE_RULE=spice_rule(ctx.get("spice", 3))),
        prompt=prompts.render(
            "critic_turn",
            MOOD=ctx["MOOD"],
            MODE=mode,
            OFFLIMITS=ctx["OFFLIMITS"],
            PROTECTED=ctx["PROTECTED"],
            RECENT_LINES=ctx["RECENT_LINES"],
            TRANSCRIPT="\n".join(ctx["TRANSCRIPT"].splitlines()[-14:]),
            TARGET=ctx["TARGET"],
            KIND="meme captions" if is_meme else ("voice note" if d["action"] == "VOICE" else "text message"),
            CANDIDATES="\n".join(f"{i}. {c['text']}" for i, c in enumerate(survivors)),
        ),
        effort=s.critic_effort,
        max_tokens=3000,
    ), s.critic_timeout_seconds)
    if out is None:
        await _log_blocked(st["group_id"], st["now"], blocked)
        return {"chosen": survivors[0] if called else None}

    reviews = {r.index: r for r in out.reviews if 0 <= r.index < len(survivors)}
    blocked += [(survivors[i]["text"], f"critic: {r.note[:150]}") for i, r in reviews.items() if not r.safe]
    await _log_blocked(st["group_id"], st["now"], blocked)
    safe = [i for i in range(len(survivors)) if reviews.get(i) is None or reviews[i].safe]
    if not safe:
        return {"chosen": fallback(rt, mode) if called and not is_meme else None}

    best = out.best if out.best in safe else max(safe, key=lambda i: reviews[i].score if i in reviews else 0)
    score = reviews[best].score if best in reviews else 0
    if not called and (out.best < 0 or score < s.critic_min_score):
        log.info("critic passed on all candidates (best score %s)", score)
        return {"chosen": None}
    return {"chosen": survivors[best]}


async def _log_blocked(group_id: int, now, blocked: list[tuple[str, str]]) -> None:
    if not blocked:
        return
    async with db.session() as ses:
        for text, reason in blocked:
            ses.add(BlockedLine(group_id=group_id, ts=now, text=text, reason=reason))
            log.info("blocked line (%s): %s", reason, text)
