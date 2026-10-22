from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_cache: dict[str, tuple[float, str]] = {}


def load(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    mtime = path.stat().st_mtime
    hit = _cache.get(name)
    if hit and hit[0] == mtime:
        return hit[1]
    text = path.read_text(encoding="utf-8").strip()
    _cache[name] = (mtime, text)
    return text


def render(name: str, **values: object) -> str:
    text = load(name)
    for key, value in values.items():
        text = text.replace("{" + key.upper() + "}", "" if value is None else str(value))
    return text
