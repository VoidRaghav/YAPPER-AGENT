import argparse
import asyncio
import base64
import io
import time

from PIL import Image, ImageDraw
from pydantic import BaseModel

from app.config import get_settings
from app.llm import make_llm
from scripts.common import setup_logging


class Ping(BaseModel):
    reply: str
    language: str


class Seen(BaseModel):
    colour: str
    text: str


def sample_image() -> str:
    img = Image.new("RGB", (240, 120), (255, 140, 0))
    ImageDraw.Draw(img).text((20, 50), "CHAI TIME", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


async def main(args: argparse.Namespace) -> int:
    setup_logging("WARNING")
    s = get_settings()
    print(f"LLM_PROVIDER={s.llm_provider}")
    if s.missing_keys():
        print(f"missing keys: {', '.join(s.missing_keys())}")
    router = make_llm(s)
    roles = {
        "writer": s.writer_model, "awards": s.awards_model, "critic": s.critic_model, "gate": s.gate_model,
        "mood": s.mood_model, "memory": s.memory_model, "trends": s.trends_model, "backup": s.backup_model,
    }
    models: dict[str, list[str]] = {}
    for role, model in roles.items():
        if model:
            models.setdefault(model, []).append(role)

    failures = 0
    for model, used_by in models.items():
        provider, bare = s.resolve(model)
        started = time.monotonic()
        out = await router.client(provider).parse(
            Ping, model=bare, system="You are a fun desi friend in a WhatsApp group.",
            prompt="Say hi to the group in one short Hinglish line, and name the language you used.",
            effort="low", max_tokens=2048, creative=True,
        )
        took = time.monotonic() - started
        status = "ok  " if out else "FAIL"
        failures += out is None
        print(f"{status} {model:28} ({', '.join(used_by)}) {took:4.1f}s  {out.reply if out else ''}")

    if s.image_understanding and not args.skip_vision:
        provider, bare = s.resolve(s.vision_model)
        started = time.monotonic()
        out = await router.client(provider).parse(
            Seen, model=bare, system="You describe images.", prompt="What is the background colour, and what text is written in the image?",
            images=[("image/jpeg", sample_image())], effort="low", max_tokens=2048,
        )
        took = time.monotonic() - started
        failures += out is None
        print(f"{'ok  ' if out else 'FAIL'} {s.vision_model:28} (vision) {took:4.1f}s  {f'{out.colour} / {out.text}' if out else ''}")

    print("all good" if not failures else f"{failures} check(s) failed, see the warnings above")
    return 1 if failures else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Check that every model in your setup answers (one tiny request each).")
    p.add_argument("--skip-vision", action="store_true")
    raise SystemExit(asyncio.run(main(p.parse_args())))
