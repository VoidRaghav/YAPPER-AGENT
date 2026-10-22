import asyncio
import base64
import io
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageOps
from pydantic import BaseModel

from app import prompts

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)


class ImageNote(BaseModel):
    description: str


def _shrink(raw: bytes, edge: int = 1280) -> str:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    img.thumbnail((edge, edge))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


async def describe(rt: "Runtime", raw: bytes) -> str | None:
    s = rt.s
    if not s.image_understanding or not raw:
        return None
    try:
        data = await asyncio.to_thread(_shrink, raw)
    except Exception as e:
        log.info("unreadable image: %s", e)
        return None
    out = await rt.llm.parse(
        ImageNote,
        model=s.vision_model,
        system=prompts.render("image"),
        prompt="Describe this image for the group-chat log.",
        images=[("image/jpeg", data)],
        effort=s.fast_effort,
        max_tokens=1000,
    )
    return out.description.strip() if out else None
