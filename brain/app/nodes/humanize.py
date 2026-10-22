import random
import re

from app.config import Settings

EMOJI = re.compile(
    "(?:[\U0001F1E6-\U0001F1FF]{2}|[\U0001F300-\U0001FAFF☀-➿⬀-⯿⌀-⏿])"
    "[️\U0001F3FB-\U0001F3FF]?(?:‍[\U0001F300-\U0001FAFF☀-➿][️]?)*"
)
WRAP_QUOTES = {('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")}


def limit_emoji(text: str, n: int = 1) -> str:
    count = 0

    def keep(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return m.group(0) if count <= n else ""

    return EMOJI.sub(keep, text)


def count_emoji(text: str) -> int:
    return len(EMOJI.findall(text))


def _casing(text: str) -> str:
    out = []
    for tok in re.split(r"(\s+)", text):
        shout = len(tok) > 1 and tok.isupper() and any(c.isalpha() for c in tok)
        out.append(tok if shout or tok.startswith(("http", "@")) else tok.lower())
    return "".join(out)


def clean(text: str, names: list[str], lowercase: bool = True, max_emoji: int = 1) -> str:
    t = (text or "").strip()
    if names:
        t = re.sub(r"^(?:" + "|".join(re.escape(n) for n in names) + r")\s*:\s*", "", t, flags=re.IGNORECASE)
    if len(t) > 1 and (t[0], t[-1]) in WRAP_QUOTES:
        t = t[1:-1].strip()
    t = t.replace("—", ", ").replace("–", "-")
    t = re.sub(r"\*\*|__|`", "", t)
    t = re.sub(r"(?m)^\s*(?:[-*•]|#{1,6})\s+", "", t)
    t = "\n".join(line for line in (re.sub(r"[ \t]+", " ", raw).strip() for raw in t.splitlines()) if line)
    if lowercase:
        t = _casing(t)
        t = re.sub(r"(?<!\.)\.\s*$", "", t)
    t = re.sub(r" ,", ",", limit_emoji(t, max_emoji))
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def reply_delay(text: str, settings: Settings, rng: random.Random, kind: str = "text") -> float:
    if kind == "reaction":
        return rng.uniform(2, 9)
    reading = rng.uniform(1.5, 5.0)
    if kind == "voice":
        effort = rng.uniform(7, 13)
    elif kind in ("image", "sticker"):
        effort = rng.uniform(4, 10)
    else:
        effort = len(text) / max(settings.typing_cps, 1) * rng.uniform(0.8, 1.25)
    return max(settings.reply_delay_min, min(settings.reply_delay_max, reading + effort))
