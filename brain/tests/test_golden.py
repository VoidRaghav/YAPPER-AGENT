from sqlalchemy import select

from app.db import BlockedLine, Group, Person, db
from app.nodes.critic import FALLBACKS
from app.nodes.gate import SERIOUS_DECLINES
from app.safety import LAUGHTER, sensitive_categories
from tests.conftest import PEOPLE, candidates, critic_pick, sent_texts

ROASTY = candidates("lol rip papa 💀", "skill issue bro", "hospital ka khana > tera khana")


async def test_serious_mention_never_gets_a_roast(chat, rt, llm):
    llm.on("Candidates", ROASTY)
    llm.on("CriticOut", critic_pick(best=0))
    await chat.say("dev", "guys papa hospital mein admit hain, ICU mein")
    await chat.say("aman", "@yapper kuch bol", mention=True)
    result = await chat.think()
    assert result["mood"] == "serious"
    texts = sent_texts(rt)
    assert len(texts) == 1
    assert texts[0] in FALLBACKS["support"]
    assert not LAUGHTER.search(texts[0])
    async with db.session() as ses:
        reasons = (await ses.scalars(select(BlockedLine.reason))).all()
    assert all(r == "joke_in_support" for r in reasons) and len(reasons) == 3


async def test_serious_mood_blocks_unsolicited_roasts(chat, rt, llm):
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.97, "target": "", "emoji": "", "mode": "roast", "reason": "opening"})
    llm.on("Candidates", ROASTY)
    llm.on("CriticOut", critic_pick())
    await chat.say("dev", "interview went badly guys. feeling low")
    result = await chat.think()
    assert result["decision"]["reason"] == "serious mood"
    assert rt.gateway.sent == []


async def test_serious_mood_allows_only_supportive_reactions(chat, rt, llm):
    llm.on("GateOut", {"action": "REACT", "confidence": 0.9, "target": "", "emoji": "😂", "mode": "banter", "reason": "x"})
    await chat.say("dev", "we broke up yesterday")
    assert (await chat.think())["decision"]["reason"] == "serious mood"
    llm.on("GateOut", {"action": "REACT", "confidence": 0.9, "target": "", "emoji": "🫂", "mode": "support", "reason": "x"})
    await chat.say("dev", "bahut bura lag raha hai", gap=40)
    await chat.think()
    assert sent_texts(rt, "reaction") == ["🫂"]


async def test_llm_detected_venting_is_respected(chat, rt, llm):
    llm.on("MoodOut", {"mood": "venting", "distressed_person": "Rahul", "reason": "upset"})
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.8, "target": "", "emoji": "", "mode": "roast", "reason": "x"})
    await chat.say("rahul", "kuch theek nahi chal raha yaar life mein")
    assert (await chat.think())["decision"]["reason"] == "serious mood"
    async with db.session() as ses:
        rahul = await ses.scalar(select(Person).where(Person.wa_jid == PEOPLE["rahul"]))
    assert rahul.protected_until is not None


async def test_sensitive_roasts_blocked_in_playful_mood(chat, rt, llm):
    llm.on("Candidates", candidates("aman is so fat the chair cried", "aman's mom ko bata dunga", "typical gareeb move"))
    llm.on("CriticOut", critic_pick(best=0))
    await chat.say("aman", "@yapper roast me, i can take it", mention=True)
    await chat.think()
    texts = sent_texts(rt)
    assert texts and texts[0] in FALLBACKS["default"]
    assert sensitive_categories(texts[0]) == []


async def test_protected_person_not_roasted_after_serious_moment(chat, rt, llm):
    await chat.say("dev", "got laid off today")
    await chat.think()
    llm.on("MoodOut", {"mood": "playful", "distressed_person": "", "reason": "banter resumed"})
    llm.on("Candidates", candidates("dev ka typing speed bhi slow hai", "aman is late as usual"))
    llm.on("CriticOut", critic_pick(best=0))
    rt.busy_until.clear()
    for i in range(4):
        await chat.say("aman", f"chalo match dekhte hai {i}", gap=300)
    await chat.say("priya", "@yapper roast someone", mention=True, gap=60)
    await chat.think()
    assert sent_texts(rt)[-1] == "aman is late as usual"


async def test_critic_unsafe_verdict_is_never_sent(chat, rt, llm):
    llm.on("Candidates", candidates("borderline line", "clean line"))
    llm.on("CriticOut", critic_pick(best=0, unsafe=(0,)))
    await chat.say("aman", "@yapper", mention=True)
    await chat.think()
    assert sent_texts(rt) == ["clean line"]


async def test_forced_roast_declined_in_serious_mood(chat, rt, llm):
    await chat.say("dev", "nani passed away last night")
    await chat.say("aman", "!roast @dev", mentions=[PEOPLE["dev"]])
    texts = sent_texts(rt)
    assert texts and texts[-1] in SERIOUS_DECLINES
    assert llm.called("Candidates") == 0


async def test_group_offlimits_enforced_in_code(chat, rt, llm):
    await chat.say("aman", "!offlimits group exams")
    async with db.session() as ses:
        assert (await ses.scalar(select(Group))).offlimits == ["exams"]
    llm.on("Candidates", candidates("aman ke exams wale excuses", "aman late again"))
    llm.on("CriticOut", critic_pick(best=0))
    rt.busy_until.clear()
    await chat.say("priya", "@yapper", mention=True, gap=60)
    await chat.think()
    assert sent_texts(rt)[-1] == "aman late again"
