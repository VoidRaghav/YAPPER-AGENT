import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

from app.config import Settings
from app.utils import utcnow

log = logging.getLogger(__name__)


def describe(job: dict[str, Any]) -> str:
    kind = job.get("kind")
    if kind == "text":
        return job.get("text", "")
    if kind == "reaction":
        return f"reacts {job.get('emoji')} to {job.get('quote', {}).get('id')}"
    size = len(job.get("media_b64") or "") * 3 // 4
    caption = f" caption={job['text']!r}" if job.get("text") else ""
    return f"<{kind} {size // 1024}KB{caption}>"


class HttpGateway:
    dry_run = False

    def __init__(self, settings: Settings):
        headers = {"x-internal-token": settings.internal_token} if settings.internal_token else {}
        self.client = httpx.AsyncClient(base_url=settings.gateway_url, timeout=30, headers=headers)

    async def send(self, job: dict[str, Any]) -> bool:
        try:
            res = await self.client.post("/send", json=job)
        except httpx.HTTPError as e:
            log.error("gateway unreachable: %s", e)
            return False
        if res.status_code != 202:
            log.error("gateway refused %s: %s %s", job.get("kind"), res.status_code, res.text[:200])
            return False
        return True

    async def close(self) -> None:
        await self.client.aclose()


class DryRunGateway:
    dry_run = True

    def __init__(self, settings: Settings, echo: Callable[[dict[str, Any]], None] | None = None):
        self.sent: list[dict[str, Any]] = []
        self.echo = echo
        self.path = settings.data_dir / "dry_run.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def send(self, job: dict[str, Any]) -> bool:
        self.sent.append(job)
        line = describe(job)
        log.info("[DRY_RUN] %s %s (delay %.1fs): %s", job.get("jid"), job.get("kind"), job.get("delay_ms", 0) / 1000, line)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": utcnow().isoformat(), "jid": job.get("jid"), "kind": job.get("kind"), "text": line}, ensure_ascii=False) + "\n")
        if self.echo:
            self.echo(job)
        return True

    async def close(self) -> None:
        return None
