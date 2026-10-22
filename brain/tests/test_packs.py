import json

import pytest
from sqlalchemy import select

from app.db import Group, db
from app.packs import Pack, PackBook, effective_spice, style_text
from tests.conftest import candidates, critic_pick, sent_texts

SHIPPED = {"friends", "family", "study", "work", "community"}


async def _group() -> Group:
    async with db.session() as ses:
        return await ses.scalar(select(Group))


def test_shipped_packs_load_and_template_is_skipped(settings):
    book = PackBook(settings)
    assert set(book.packs) == SHIPPED
    assert book.get("nope").id == "friends"
    assert book.get(None).id == "friends"


def test_custom_packs_are_validated_and_clamped(settings, tmp_path):
    extra = tmp_path / "packs"
    extra.mkdir()
    (extra / "hostel.json").write_text(json.dumps({"id": "hostel", "name": "Hostel", "roasts": True, "vibe": {"spice": 9, "emoji": 7, "chattiness": -3}}))
    (extra / "broken.json").write_text("{ not json")
    settings.extra_packs_dir = str(extra)
    book = PackBook(settings)
    hostel = book.packs["hostel"]
    assert (hostel.vibe.spice, hostel.vibe.emoji, hostel.vibe.chattiness) == (5, 3, 0)
    assert "broken" not in book.packs


def test_spice_is_capped_when_a_pack_disallows_roasts():
    family = Pack(id="f", name="F", roasts=False, vibe={"spice": 1})
    friends = Pack(id="x", name="X", roasts=True, vibe={"spice": 4})
    assert effective_spice(family, 5) == 2
    assert effective_spice(friends, None) == 4
    assert effective_spice(friends, 9) == 5


def test_style_follows_formality_and_emoji():
    casual = style_text(Pack(id="a", name="A", vibe={"formality": 0, "emoji": 0}), "hinglish")
    formal = style_text(Pack(id="b", name="B", vibe={"formality": 5, "emoji": 2}), "english")
    assert "lowercase" in casual and "No emoji" in casual and "Hinglish" in casual
    assert "professional" in formal and "never more than 2" in formal


async def test_vibe_switch_is_owner_only_and_changes_behaviour(chat, rt, settings):
    settings.shared_number, settings.owner_name = True, "Kabir"
    await chat.say("aman", "!vibe")
    assert "family" in sent_texts(rt)[-1] and "friends (current)" in sent_texts(rt)[-1]
    await chat.say("aman", "!vibe family")
    assert (await _group()).pack is None
    await chat.say("kabir", "!vibe family", from_owner=True)
    group = await _group()
    assert (group.pack, group.chattiness, group.language_mix) == ("family", "med", "hinglish")
    await chat.say("aman", "!roast @priya")
    assert "roast mode band" in sent_texts(rt)[-1]


async def test_spice_command_respects_the_pack(chat, rt, settings):
    await chat.say("aman", "!spice 5")
    assert sent_texts(rt)[-1] == "spice level: 5/5"
    await chat.say("aman", "!vibe family")
    await chat.say("aman", "!spice 5", gap=60)
    assert sent_texts(rt)[-1].startswith("spice level: 2/5")


async def test_family_pack_turns_roast_mode_into_banter(chat, rt, llm):
    systems: list[str] = []
    inner = llm.parse

    async def spy(schema, **kwargs):
        if schema.__name__ == "GateOut":
            systems.append(kwargs["system"])
        return await inner(schema, **kwargs)

    llm.parse = spy
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.95, "target": "", "emoji": "", "mode": "roast", "reason": "setup"})
    llm.on("Candidates", candidates("ab family trips mein ek driver aur badh gaya"))
    llm.on("CriticOut", critic_pick())
    await chat.say("kabir", "!vibe family")
    await chat.say("rohan", "cleared my driving test!!", gap=60)
    result = await chat.think()
    assert result["decision"]["mode"] == "banter"
    assert "comeback" not in systems[-1] and "good news" in systems[-1]


async def test_pack_off_limits_are_enforced_in_code(chat, rt, llm):
    llm.on("Candidates", candidates("marriage kab kar rahe ho rohan", "congratulations Rohan!"))
    llm.on("CriticOut", critic_pick(best=0))
    await chat.say("kabir", "!vibe family")
    await chat.say("rohan", "yapper guess what", mention=True, gap=60)
    await chat.think()
    assert sent_texts(rt)[-1] == "congratulations Rohan!"


async def test_work_pack_keeps_capitals_and_drops_emoji(chat, rt, llm):
    llm.on("Candidates", candidates("Nice work Arjun. Signups will thank you 🎉"))
    llm.on("CriticOut", critic_pick())
    await chat.say("kabir", "!vibe work")
    await chat.say("arjun", "yapper shipped the onboarding flow", gap=60)
    await chat.think()
    assert sent_texts(rt)[-1] == "Nice work Arjun. Signups will thank you"
    await chat.say("arjun", "!meme launch day", gap=60)
    assert sent_texts(rt)[-1] == "memes are off in this group"


@pytest.mark.parametrize(
    ("pack", "jid", "expect", "absent"),
    [
        ("friends", "g1@g.us", ["entry maar di", "main AI hoon", "Kabir ke number se", "on/off ka remote Kabir"], []),
        ("work", "g1@g.us", ["Hi team", "I'm an AI and I read every message", "start with 🤖", "Only Kabir can switch me"], ["main AI hoon"]),
        ("friends", "tg:-1001", ["main AI hoon"], ["ke number se"]),
    ],
)
def test_intro_matches_pack_language_and_platform(rt, settings, pack, jid, expect, absent):
    settings.shared_number, settings.owner_name = True, "Kabir"
    text = rt.intro_text(rt.packs.get(pack), jid)
    assert len(text.splitlines()) == 3
    for piece in expect:
        assert piece in text
    for piece in absent:
        assert piece not in text
