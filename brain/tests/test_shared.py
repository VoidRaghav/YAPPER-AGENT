from app.config import Settings
from tests.conftest import sent_texts


def test_shared_number_only_limits_whatsapp_groups(rt, settings):
    settings.shared_number = True
    assert rt.can_unlabelled("120363000@g.us") is False
    assert rt.can_unlabelled("tg:-1001234") is True
    assert rt.can_unlabelled("dc:998877") is True
    settings.shared_number = False
    assert rt.can_unlabelled("120363000@g.us") is True


async def test_intro_explains_the_bot_label(chat, rt, settings):
    settings.shared_number = True
    settings.owner_name = "Kabir"
    settings.intro_on_add = True
    await chat.say("aman", "hello")
    intro = sent_texts(rt)[0]
    assert "Kabir ke number se" in intro and "🤖 wale messages mere" in intro and "'yapper'" in intro
    assert len(intro.splitlines()) == 3 and len(intro) < 400


async def test_dry_run_intro_is_not_marked_sent_so_going_live_still_introduces(chat, rt, settings):
    from sqlalchemy import select

    from app.db import Group, db

    settings.intro_on_add = True
    await chat.say("aman", "hello")
    await chat.say("aman", "hello again")
    assert sum(1 for j in rt.gateway.sent if j.get("text", "").startswith("entry maar di")) == 1
    async with db.session() as ses:
        assert (await ses.scalar(select(Group))).intro_sent is False


async def test_first_question_is_answered_after_the_intro(chat, rt, llm, settings):
    from tests.conftest import candidates, critic_pick

    settings.shared_number = True
    settings.intro_on_add = True
    llm.on("Candidates", candidates("trip ka scene? plan banta hai, execution kabhi nahi"))
    llm.on("CriticOut", critic_pick())
    await chat.say("kabir", "Yapper kya scene hai trip ka")
    result = await chat.think()
    assert result["decision"]["reason"] == "addressed"
    texts = sent_texts(rt)
    assert texts[0].startswith("entry maar di") and texts[1] == "trip ka scene? plan banta hai, execution kabhi nahi"


async def test_intro_without_shared_mode_has_no_owner_note(chat, rt, settings):
    settings.intro_on_add = True
    await chat.say("aman", "hello")
    assert "ke number se" not in sent_texts(rt)[0]


async def test_no_reactions_in_shared_mode(chat, rt, llm):
    rt.s = Settings(**{**rt.s.model_dump(), "shared_number": True})
    llm.on("GateOut", {"action": "REACT", "confidence": 0.95, "target": "", "emoji": "💀", "mode": "banter", "reason": "funny"})
    await chat.say("rahul", "i fell asleep at my own birthday party")
    assert (await chat.think())["decision"]["reason"] == "reactions off"
    assert rt.gateway.sent == []


async def test_memes_go_out_as_captionable_images_in_shared_mode(chat, rt, llm):
    rt.s = Settings(**{**rt.s.model_dump(), "shared_number": True})
    llm.on("MemeOptions", {"options": [{"template": "quote", "captions": ["kal se pakka", "- rahul, 2026"], "sticker": True, "ref": ""}]})
    llm.on("CriticOut", lambda p: {"reviews": [{"index": 0, "safe": True, "score": 8, "note": "x"}], "best": 0})
    await chat.say("rahul", "kal se pakka gym")
    await chat.say("aman", "!meme rahul ka gym plan")
    assert rt.gateway.sent[-1]["kind"] == "image"
