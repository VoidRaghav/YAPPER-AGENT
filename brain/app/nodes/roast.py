from typing import TYPE_CHECKING

from pydantic import BaseModel

from app import prompts
from app.nodes import meme
from app.packs import effective_spice, examples_text, spice_rule, style_text

if TYPE_CHECKING:
    from app.runtime import Runtime

MODE_GUIDES = {
    "banter": "Playful and observational, like a friend jumping into the flow of the chat.",
    "roast": "Roast the behaviour, take, excuse or habit, never the person. Affectionate savage, the kind they screenshot and forward.",
    "hype": "Someone pulled something off. Celebrate loudly and specifically, then slip in one friendly tease.",
    "support": "Someone is having a hard time. Be warm, brief and specific to what they said. No jokes, no memes, no advice dumps, no silver linings. All three candidates must be supportive.",
    "comeback": "You just got roasted. Fire back harder or concede with style. Never defensive, never hurt.",
    "answer": "You were called directly. If it's a real question, actually answer it in a line or two, with personality. If they're roasting you, fire back or concede with style. If they're just poking you, banter back.",
}

VOICE_NOTE = "This goes out as a short voice note, so write it the way you'd say it out loud: max about 20 words, no emoji, natural spoken Hinglish."
TEXT_NOTE = "This goes out as a normal WhatsApp text."


class Candidate(BaseModel):
    text: str
    ref: str
    angle: str


class Candidates(BaseModel):
    candidates: list[Candidate]


def persona(rt: "Runtime", ctx: dict) -> str:
    s = rt.s
    pack = rt.packs.get(ctx.get("pack_id"))
    spice = ctx.get("spice", effective_spice(pack, None))
    return prompts.render(
        "persona",
        BOT_NAME=s.bot_name,
        PLATFORM=ctx.get("platform", "chat"),
        GROUP_NAME=ctx["GROUP_NAME"],
        ROLE=pack.role,
        PERSONA=pack.persona.replace("{BOT_NAME}", s.bot_name),
        STYLE=style_text(pack, ctx.get("language") or pack.language),
        SPICE_RULE=spice_rule(spice),
        EXAMPLES=examples_text(pack, s.bot_name),
    )


def mode_guide(mode: str, spice: int) -> str:
    if spice < 3 and mode in ("roast", "comeback"):
        mode = "banter"
    return MODE_GUIDES.get(mode, MODE_GUIDES["banter"])


async def write_node(rt: "Runtime", st: dict) -> dict:
    s = rt.s
    ctx = st["context"]
    d = st["decision"]
    system = persona(rt, ctx)
    if d["action"] == "MEME":
        return await meme.plan_node(rt, st, system)
    out = await rt.llm.parse(
        Candidates,
        model=s.writer_model,
        system=system,
        prompt=prompts.render(
            "writer",
            **{k: v for k, v in ctx.items() if k.isupper()},
            MODE_GUIDE=mode_guide(d.get("mode", "banter"), ctx.get("spice", 3)),
            FORMAT=VOICE_NOTE if d["action"] == "VOICE" else TEXT_NOTE,
        ),
        effort=s.writer_effort,
        max_tokens=8000,
        creative=True,
    )
    return {"candidates": [c.model_dump() for c in out.candidates[:4]] if out else []}
