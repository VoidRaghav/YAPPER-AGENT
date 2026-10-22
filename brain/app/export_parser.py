import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TIME = r"(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap]\.?\s?[Mm]\.?)?)"
IOS = re.compile(r"^\[(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s*" + TIME + r"\]\s(.+?):\s(.*)$")
ANDROID = re.compile(r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s*" + TIME + r"\s[-–]\s(.+?):\s(.*)$")
STAMPED = re.compile(r"^\[?\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},?\s*" + TIME + r"(?:\]|\s[-–])\s")
MEDIA = {
    "image omitted": "image",
    "photo omitted": "image",
    "audio omitted": "voice",
    "voice omitted": "voice",
    "sticker omitted": "sticker",
    "video omitted": "video",
    "gif omitted": "gif",
    "document omitted": "document",
    "<media omitted>": "image",
}


@dataclass
class ExportedMessage:
    ts: datetime
    sender: str
    text: str
    kind: str = "text"


def _normalize(line: str) -> str:
    return line.replace("‎", "").replace("‏", "").replace(" ", " ").replace(" ", " ").rstrip("\r\n")


def _when(date: str, time: str, dayfirst: bool) -> datetime:
    a, b, y = (int(x) for x in re.split(r"[/.-]", date))
    day, month = (a, b) if dayfirst else (b, a)
    year = y + 2000 if y < 100 else y
    t = time.upper().replace(".", "").replace(" ", "")
    pm, am = t.endswith("PM"), t.endswith("AM")
    parts = [int(x) for x in t.rstrip("APM").split(":")]
    hour, minute, second = parts[0], parts[1], parts[2] if len(parts) > 2 else 0
    if pm and hour < 12:
        hour += 12
    if am and hour == 12:
        hour = 0
    return datetime(year, month, day, hour, minute, second)


def parse_export(text: str, dayfirst: bool = True) -> list[ExportedMessage]:
    out: list[ExportedMessage] = []
    for raw in text.splitlines():
        line = _normalize(raw)
        m = IOS.match(line) or ANDROID.match(line)
        if m:
            date, time, sender, body = m.groups()
            try:
                ts = _when(date, time, dayfirst)
            except ValueError:
                ts = _when(date, time, not dayfirst)
            body = body.strip()
            kind = MEDIA.get(body.lower(), "text")
            out.append(ExportedMessage(ts, sender.strip(), "" if kind != "text" else body, kind))
        elif out and line.strip() and not STAMPED.match(line):
            out[-1].text = f"{out[-1].text}\n{line}".strip()
    return out


def load_export(path: str | Path, dayfirst: bool = True) -> list[ExportedMessage]:
    return parse_export(Path(path).read_text(encoding="utf-8", errors="ignore"), dayfirst)
