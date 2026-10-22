import io

import pytest
from PIL import Image

from app.config import Settings
from app.nodes import meme


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(_env_file=None, meme_templates_dir=tmp_path)


@pytest.mark.parametrize("template", list(meme.BUILTIN))
def test_builtin_templates_render(s, template):
    img = meme.build(s, template, ["aman reached on time", "sources: nobody believes it", "more at 9"])
    assert img.width >= 800 and img.height >= 600


def test_sticker_is_whatsapp_compliant(s):
    data = meme.to_sticker(meme.build(s, "quote", ["main kal se gym jaunga", "- rahul, 2026"]))
    img = Image.open(io.BytesIO(data))
    assert img.format == "WEBP"
    assert img.size == (512, 512)
    assert len(data) <= 100_000


def test_topbottom_uses_base_image(s, tmp_path):
    base = tmp_path / "base.jpg"
    Image.new("RGB", (640, 480), (200, 30, 30)).save(base)
    img = meme.build(s, "topbottom", ["top", "bottom"], str(base))
    assert img.getpixel((5, 540))[0] > 150


def test_custom_template(s, tmp_path):
    Image.new("RGB", (800, 600), (10, 120, 10)).save(tmp_path / "custom.jpg")
    (tmp_path / "templates.json").write_text('[{"name": "babu_rao", "file": "custom.jpg", "description": "confused", "boxes": [{"x": 0.05, "y": 0.02, "w": 0.9, "h": 0.2}]}]')
    assert "babu_rao" in meme.template_menu(s, False)
    img = meme.build(s, "babu_rao", ["ye kya ho raha hai"])
    assert img.size == (800, 600)


async def test_render_plan(s):
    data, kind = await meme.render_plan(s, {"template": "breaking", "captions": ["aman on time", "historic"], "sticker": False})
    assert kind == "image" and data[:2] == b"\xff\xd8"
    data, kind = await meme.render_plan(s, {"template": "quote", "captions": ["kal se pakka"], "sticker": True})
    assert kind == "sticker"
