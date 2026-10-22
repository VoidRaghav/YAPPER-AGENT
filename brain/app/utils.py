import re
import unicodedata
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def from_unix(ts: float | int | None) -> datetime:
    if not ts:
        return utcnow()
    return datetime.fromtimestamp(float(ts), UTC).replace(tzinfo=None)


def to_unix(dt: datetime) -> float:
    return dt.replace(tzinfo=UTC).timestamp()


def to_local(dt: datetime, tz: str) -> datetime:
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))


def local_midnight_utc(now: datetime, tz: str) -> datetime:
    local = to_local(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC).replace(tzinfo=None)


def week_bounds(now: datetime, tz: str) -> tuple[datetime, datetime, str]:
    local = to_local(now, tz)
    start_local = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(UTC).replace(tzinfo=None)
    iso = start_local.isocalendar()
    return start, now, f"{iso.year}-W{iso.week:02d}"


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "item"


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9ऀ-ॿ]+", text.lower()) if len(w) > 2}


def clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def hhmm(dt: datetime, tz: str) -> str:
    return to_local(dt, tz).strftime("%a %H:%M")
