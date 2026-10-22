import asyncio
import io
import json
import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel

from app import prompts
from app.config import Settings

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

SIZE = 1080
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

BUILTIN = {
    "breaking": "Indian news-channel BREAKING NEWS screen. captions: [headline, sub-headline, ticker]. Perfect for dramatising something trivial a friend did like it's national news.",
    "twopanel": "Drake-style reject/approve. captions: [the thing rejected, the thing preferred].",
    "expectation_reality": "Expectation vs reality split. captions: [expectation, reality].",
    "topbottom": "Classic top text / bottom text. captions: [top, bottom]. Uses the replied-to image when there is one.",
    "certificate": "Mock-formal certificate. captions: [recipient name, what they achieved].",
    "quote": "Famous-quote card of something a friend said. captions: [the quote, '- name, year']. Great as a sticker.",
}


class MemeOption(BaseModel):
    template: str
    captions: list[str]
    sticker: bool
    ref: str


class MemeOptions(BaseModel):
    options: list[MemeOption]


@lru_cache
def _font_path(custom: str) -> str | None:
    for path in [custom, *FONT_CANDIDATES]:
        if path and Path(path).exists():
            return path
    return None


def _font(s: Settings, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path(s.font_path)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return [ln for ln in lines if ln]


def draw_box(
    s: Settings,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    *,
    max_size: int = 96,
    min_size: int = 18,
    fill: str | tuple = "white",
    stroke: int = 0,
    stroke_fill: str | tuple = "black",
    align: str = "center",
    valign: str = "middle",
) -> None:
    x, y, w, h = box
    text = text.strip()
    if not text:
        return
    for size in range(max_size, min_size - 1, -2):
        font = _font(s, size)
        lines = _wrap(draw, text, font, w - 2 * stroke)
        line_h = int(size * 1.18)
        if len(lines) * line_h <= h and all(draw.textlength(ln, font=font) <= w for ln in lines):
            break
    total = len(lines) * line_h
    top = y + {"top": 0, "bottom": h - total}.get(valign, (h - total) // 2)
    for i, ln in enumerate(lines):
        lw = draw.textlength(ln, font=font)
        lx = x + {"left": 0, "right": w - lw}.get(align, (w - lw) / 2)
        draw.text((lx, top + i * line_h), ln, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = ImageDraw.Draw(img)
    for row in range(h):
        t = row / max(h - 1, 1)
        px.line([(0, row), (w, row)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return img


def _cap(captions: list[str], i: int, default: str = "") -> str:
    return captions[i] if len(captions) > i and captions[i].strip() else default


def breaking(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = base.resize((SIZE, SIZE)) if base else _gradient((SIZE, SIZE), (18, 24, 66), (92, 10, 18))
    d = ImageDraw.Draw(img)
    if not base:
        for i in range(-SIZE, SIZE, 90):
            d.line([(i, 0), (i + SIZE, SIZE)], fill=(58, 44, 96), width=3)
    d.rectangle((40, 40, 190, 110), fill=(220, 20, 30))
    draw_box(s, d, (40, 40, 150, 70), "LIVE", max_size=44)
    d.rectangle((SIZE - 330, 40, SIZE - 40, 120), fill=(250, 204, 21))
    draw_box(s, d, (SIZE - 320, 40, 270, 80), "YAPPER TAK", max_size=46, fill=(150, 10, 20))
    draw_box(s, d, (70, 170, SIZE - 140, 470), _cap(captions, 0, "big news").upper(), max_size=104, stroke=5)
    d.rectangle((0, 690, SIZE, 790), fill=(210, 16, 28))
    draw_box(s, d, (40, 690, SIZE - 80, 100), "BREAKING NEWS", max_size=72, align="left")
    d.rectangle((0, 790, SIZE, 930), fill=(250, 204, 21))
    draw_box(s, d, (40, 795, SIZE - 80, 130), _cap(captions, 1, "sources confirm"), max_size=60, fill=(20, 20, 20), align="left")
    d.rectangle((0, 960, SIZE, 1040), fill=(12, 12, 12))
    draw_box(s, d, (40, 960, SIZE - 80, 80), _cap(captions, 2, "nobody asked • more at 9"), max_size=38, fill=(240, 240, 240), align="left")
    return img


def twopanel(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), "white")
    d = ImageDraw.Draw(img)
    half = SIZE // 2
    d.rectangle((0, 0, SIZE, half), fill=(255, 232, 232))
    d.rectangle((0, half, SIZE, SIZE), fill=(228, 250, 232))
    d.line([(0, half), (SIZE, half)], fill=(40, 40, 40), width=6)
    d.line([(SIZE * 0.38, 0), (SIZE * 0.38, SIZE)], fill=(40, 40, 40), width=6)
    cx, r = int(SIZE * 0.19), 120
    d.line([(cx - r, half / 2 - r), (cx + r, half / 2 + r)], fill=(214, 40, 40), width=40)
    d.line([(cx - r, half / 2 + r), (cx + r, half / 2 - r)], fill=(214, 40, 40), width=40)
    cy = half + half / 2
    d.line([(cx - r, cy), (cx - r / 3, cy + r * 0.8), (cx + r, cy - r * 0.8)], fill=(30, 160, 70), width=40, joint="curve")
    tx, tw = int(SIZE * 0.42), int(SIZE * 0.54)
    draw_box(s, d, (tx, 40, tw, half - 80), _cap(captions, 0, "the old way"), max_size=72, fill=(30, 30, 30))
    draw_box(s, d, (tx, half + 40, tw, half - 80), _cap(captions, 1, "the new way"), max_size=72, fill=(30, 30, 30))
    return img


def expectation_reality(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), (250, 250, 250))
    d = ImageDraw.Draw(img)
    half = SIZE // 2
    d.rectangle((0, 0, half, 140), fill=(37, 99, 235))
    d.rectangle((half, 0, SIZE, 140), fill=(31, 31, 31))
    draw_box(s, d, (0, 0, half, 140), "EXPECTATION", max_size=64)
    draw_box(s, d, (half, 0, half, 140), "REALITY", max_size=64)
    d.line([(half, 0), (half, SIZE)], fill=(31, 31, 31), width=8)
    draw_box(s, d, (40, 190, half - 80, SIZE - 260), _cap(captions, 0, "big plans"), max_size=76, fill=(30, 30, 30))
    draw_box(s, d, (half + 40, 190, half - 80, SIZE - 260), _cap(captions, 1, "nothing happened"), max_size=76, fill=(30, 30, 30))
    return img


def topbottom(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = ImageOps.fit(base, (SIZE, SIZE)) if base else _gradient((SIZE, SIZE), (40, 40, 40), (10, 10, 10))
    d = ImageDraw.Draw(img)
    draw_box(s, d, (40, 30, SIZE - 80, 300), _cap(captions, 0).upper(), max_size=110, stroke=6, valign="top")
    draw_box(s, d, (40, SIZE - 330, SIZE - 80, 300), _cap(captions, 1).upper(), max_size=110, stroke=6, valign="bottom")
    return img


def certificate(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = Image.new("RGB", (SIZE, int(SIZE * 0.75)), (253, 246, 227))
    w, h = img.size
    d = ImageDraw.Draw(img)
    gold = (184, 134, 11)
    d.rectangle((24, 24, w - 24, h - 24), outline=gold, width=10)
    d.rectangle((48, 48, w - 48, h - 48), outline=gold, width=3)
    draw_box(s, d, (80, 80, w - 160, 90), "CERTIFICATE OF ACHIEVEMENT", max_size=58, fill=(120, 70, 0))
    draw_box(s, d, (80, 180, w - 160, 50), "proudly presented to", max_size=34, fill=(90, 90, 90))
    draw_box(s, d, (80, 240, w - 160, 130), _cap(captions, 0, "you").upper(), max_size=96, fill=(20, 20, 20))
    draw_box(s, d, (120, 380, w - 240, 170), _cap(captions, 1, "for showing up, eventually"), max_size=46, fill=(50, 50, 50))
    d.ellipse((w - 250, h - 250, w - 90, h - 90), fill=(200, 30, 40), outline=gold, width=6)
    draw_box(s, d, (w - 250, h - 250, 160, 160), "100% CERTIFIED", max_size=26)
    draw_box(s, d, (90, h - 150, 520, 60), f"- {s.bot_name.lower()}, chief roasting officer", max_size=30, fill=(90, 90, 90), align="left")
    return img


def quote(s: Settings, captions: list[str], base: Image.Image | None) -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), (17, 17, 17))
    d = ImageDraw.Draw(img)
    d.text((60, -40), "\u201c", font=_font(s, 420), fill=(55, 55, 55))
    draw_box(s, d, (90, 200, SIZE - 180, 600), _cap(captions, 0, "..."), max_size=88, fill="white")
    draw_box(s, d, (90, 830, SIZE - 180, 120), _cap(captions, 1), max_size=46, fill=(170, 170, 170), align="right")
    return img


BUILDERS: dict[str, Callable[[Settings, list[str], Image.Image | None], Image.Image]] = {
    "breaking": breaking,
    "twopanel": twopanel,
    "expectation_reality": expectation_reality,
    "topbottom": topbottom,
    "certificate": certificate,
    "quote": quote,
}


def custom_templates(s: Settings) -> dict[str, dict]:
    path = s.meme_templates_dir / "templates.json"
    if not path.exists():
        return {}
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("bad templates.json: %s", e)
        return {}
    return {t["name"]: t for t in items if (s.meme_templates_dir / t.get("file", "")).is_file()}


def render_custom(s: Settings, spec: dict, captions: list[str]) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(s.meme_templates_dir / spec["file"])).convert("RGB")
    d = ImageDraw.Draw(img)
    w, h = img.size
    for i, box in enumerate(spec.get("boxes", [])):
        text = _cap(captions, i)
        if box.get("uppercase", True):
            text = text.upper()
        stroke = max(2, int(min(w, h) * 0.006)) if box.get("stroke", True) else 0
        draw_box(
            s, d, (int(box["x"] * w), int(box["y"] * h), int(box["w"] * w), int(box["h"] * h)), text,
            max_size=int(h * box.get("max_size", 0.1)), fill=box.get("color", "white"), stroke=stroke,
        )
    return img


def template_menu(s: Settings, has_image: bool) -> str:
    lines = [f"- {name}: {desc}" for name, desc in BUILTIN.items()]
    lines += [f"- {name}: {spec.get('description', '')} captions: {len(spec.get('boxes', []))} boxes" for name, spec in custom_templates(s).items()]
    if has_image:
        lines.append("A replied-to image is available: prefer topbottom or breaking to caption it.")
    return "\n".join(lines)


def to_sticker(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    img.thumbnail((512, 512))
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.paste(img, ((512 - img.width) // 2, (512 - img.height) // 2), img)
    for q in (90, 80, 70, 60, 50, 40, 30):
        buf = io.BytesIO()
        canvas.save(buf, "WEBP", quality=q, method=6)
        if buf.tell() <= 100_000:
            break
    return buf.getvalue()


def to_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=88)
    return buf.getvalue()


def build(s: Settings, template: str, captions: list[str], base_path: str | None = None) -> Image.Image:
    base = None
    if base_path and Path(base_path).is_file():
        base = ImageOps.exif_transpose(Image.open(base_path)).convert("RGB")
    customs = custom_templates(s)
    if template in customs:
        return render_custom(s, customs[template], captions)
    builder = BUILDERS.get(template) or (topbottom if base else breaking)
    return builder(s, captions, base)


async def render_plan(s: Settings, plan: dict, base_path: str | None = None) -> tuple[bytes, str] | None:
    try:
        img = await asyncio.to_thread(build, s, plan.get("template", ""), plan.get("captions", []), base_path)
        if plan.get("sticker"):
            return await asyncio.to_thread(to_sticker, img), "sticker"
        return await asyncio.to_thread(to_jpeg, img), "image"
    except Exception as e:
        log.warning("meme render failed: %s", e)
        return None


async def sticker_from_file(path: str) -> bytes | None:
    try:
        return await asyncio.to_thread(lambda: to_sticker(ImageOps.exif_transpose(Image.open(path))))
    except Exception as e:
        log.warning("sticker conversion failed: %s", e)
        return None


async def plan_node(rt: "Runtime", st: dict, persona: str) -> dict:
    s = rt.s
    ctx = st["context"]
    out = await rt.llm.parse(
        MemeOptions,
        model=s.writer_model,
        system=persona,
        prompt=prompts.render(
            "meme",
            **{k: v for k, v in ctx.items() if k.isupper()},
            TEMPLATES=template_menu(s, bool(ctx.get("image_path"))),
        ),
        effort=s.writer_effort,
        max_tokens=8000,
        creative=True,
    )
    options = out.options[:3] if out else []
    return {
        "candidates": [
            {"text": " / ".join(c for c in o.captions if c.strip()), "ref": o.ref, "angle": o.template, "plan": o.model_dump()}
            for o in options
            if any(c.strip() for c in o.captions)
        ]
    }
