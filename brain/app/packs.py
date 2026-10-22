import logging
from pathlib import Path

from pydantic import BaseModel, field_validator

from app.config import Settings

log = logging.getLogger(__name__)

LANGUAGE_NOTES = {
    "hinglish": "Write Hinglish in Roman script the way people actually type it ('kya scene hai', 'bas kar', 'sahi hai', 'arre yaar'). Slang lands when it's natural, never stuffed in.",
    "english": "Write in natural, casual English.",
    "hindi": "Write in simple, natural Hindi in Devanagari.",
}
HINDI_LIKE = {"hinglish", "hindi"}


def _clamp(value: int, high: int) -> int:
    return max(0, min(int(value), high))


class Vibe(BaseModel):
    spice: int = 2
    chattiness: int = 2
    formality: int = 1
    emoji: int = 1

    @field_validator("spice", "chattiness", "formality")
    @classmethod
    def _five(cls, v: int) -> int:
        return _clamp(v, 5)

    @field_validator("emoji")
    @classmethod
    def _three(cls, v: int) -> int:
        return _clamp(v, 3)


class Features(BaseModel):
    memes: bool = True
    voice: bool = True
    weekly_recap: bool = True
    reactions: bool = True
    trends: bool = True


class Example(BaseModel):
    them: str
    you: str


class Pack(BaseModel):
    id: str
    name: str
    tagline: str = ""
    color: str = "#f5f5f0"
    role: str = "a member of this group"
    persona: str = ""
    vibe: Vibe = Vibe()
    language: str = "english"
    roasts: bool = False
    speak_when: list[str] = []
    quiet_when: list[str] = []
    features: Features = Features()
    off_limits: list[str] = []
    recap_style: str = "a short weekly recap with light superlatives"
    intro: str = "hi! I'm {BOT_NAME}, the new member of this group"
    examples: list[Example] = []

    @property
    def chattiness_level(self) -> str:
        return "low" if self.vibe.chattiness <= 1 else "high" if self.vibe.chattiness >= 4 else "med"

    @property
    def hindi_like(self) -> bool:
        return self.language.lower() in HINDI_LIKE


FALLBACK = Pack(id="default", name="Default", role="a friendly member of this group", persona="You're friendly, brief and helpful, with a light sense of humour.")


class PackBook:
    def __init__(self, settings: Settings):
        self.s = settings
        self._stamp: tuple = ()
        self._packs: dict[str, Pack] = {}

    def _dirs(self) -> list[Path]:
        dirs = [Path(self.s.packs_dir)]
        if self.s.extra_packs_dir:
            dirs.append(Path(self.s.extra_packs_dir))
        return [d for d in dirs if d.is_dir()]

    def _files(self) -> list[Path]:
        return [f for d in self._dirs() for f in sorted(d.glob("*.json")) if not f.name.endswith(".template.json")]

    @property
    def packs(self) -> dict[str, Pack]:
        files = self._files()
        stamp = tuple((str(f), f.stat().st_mtime) for f in files)
        if stamp != self._stamp:
            loaded: dict[str, Pack] = {}
            for f in files:
                try:
                    pack = Pack.model_validate_json(f.read_text(encoding="utf-8"))
                    loaded[pack.id] = pack
                except ValueError as e:
                    log.warning("skipping invalid pack %s: %s", f.name, e)
            self._packs, self._stamp = loaded, stamp
        return self._packs

    def get(self, pack_id: str | None) -> Pack:
        packs = self.packs
        return packs.get(pack_id or "") or packs.get(self.s.default_pack) or FALLBACK


def effective_spice(pack: Pack, override: int | None) -> int:
    spice = pack.vibe.spice if override is None else _clamp(override, 5)
    return spice if pack.roasts else min(spice, 2)


def style_text(pack: Pack, language: str) -> str:
    f = pack.vibe.formality
    if f <= 1:
        base = "Like a real person texting: short, lowercase, casual. Usually one line, sometimes two, never a paragraph. No full stop at the end."
    elif f <= 3:
        base = "Casual but clear. Normal capitalisation is fine. One or two short lines."
    else:
        base = "Polite, clear and professional. Proper sentences, still brief. Light on slang."
    lang = LANGUAGE_NOTES.get(language.lower(), f"Write in {language}.")
    n = pack.vibe.emoji
    emoji = {0: "No emoji.", 1: "At most one emoji, usually none."}.get(n, f"An emoji or two is fine, never more than {n}.")
    return f"{base} {lang} {emoji} No lists, no markdown."


def spice_rule(spice: int) -> str:
    if spice <= 0:
        return "No roasting or teasing at all. Warm, wholesome humour only."
    if spice <= 2:
        return "Gentle, friendly teasing about harmless habits at most, never at anyone's expense."
    if spice <= 4:
        return "Affectionate roasts of behaviour, takes, excuses and habits are fine."
    return "Savage but affectionate roasts of behaviour are fine, but never personal."


def examples_text(pack: Pack, bot_name: str) -> str:
    if not pack.examples:
        return "(no examples)"
    return "\n\n".join(f"{e.them.replace('{BOT_NAME}', bot_name)}\nyou: {e.you.replace('{BOT_NAME}', bot_name)}" for e in pack.examples)


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def platform_of(jid: str) -> str:
    if jid.endswith("@g.us"):
        return "WhatsApp"
    if jid.startswith("tg:"):
        return "Telegram"
    if jid.startswith("dc:"):
        return "Discord"
    return "chat"
