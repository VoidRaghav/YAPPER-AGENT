from sqlalchemy import select

from app.db import Group, Lore, Memory, Person, db
from app.nodes import memory
from tests.conftest import PEOPLE, candidates, critic_pick

EXTRACTION = {
    "memories": [
        {"person": "Aman", "kind": "habit", "text": "aman says 5 min and shows up 45 min later", "sensitive": False},
        {"person": "Aman", "kind": "fact", "text": "aman's dad is in hospital this week", "sensitive": False},
        {"person": "group", "kind": "bit", "text": "the goa plan never happens", "sensitive": False},
    ],
    "lore": [{"title": "aman time", "description": "aman's 5 minutes means 45"}],
    "nicknames": [{"person": "Aman", "nickname": "late latif"}],
    "profiles": [{"person": "Aman", "summary": "perpetually late, loves RCB"}],
}


async def _group_id() -> int:
    async with db.session() as ses:
        return (await ses.scalar(select(Group))).id


async def test_extraction_stores_and_flags_sensitive(chat, rt, llm):
    llm.on("Extraction", EXTRACTION)
    await chat.say("aman", "bas 5 min mein aaya")
    await chat.say("priya", "haha classic aman")
    stored = await memory.extract(rt, await _group_id())
    assert stored == 3
    async with db.session() as ses:
        mems = {m.text: m for m in (await ses.scalars(select(Memory))).all()}
        aman = await ses.scalar(select(Person).where(Person.wa_jid == PEOPLE["aman"]))
        lore = (await ses.scalars(select(Lore))).all()
        group = await ses.scalar(select(Group))
    assert mems["aman's dad is in hospital this week"].sensitive is True
    assert mems["aman says 5 min and shows up 45 min later"].sensitive is False
    assert mems["the goa plan never happens"].person_id is None
    assert aman.nicknames == ["late latif"]
    assert aman.profile_json["summary"] == "perpetually late, loves RCB"
    assert [x.title for x in lore] == ["aman time"]
    assert group.msgs_since_memory == 0 and group.memory_cursor > 0
    assert await memory.extract(rt, group.id) == 0


async def test_sensitive_memories_never_reach_the_writer(chat, rt, llm):
    llm.on("Extraction", EXTRACTION)
    await chat.say("aman", "hello")
    await memory.extract(rt, await _group_id())
    llm.on("Candidates", candidates("aman time strikes again"))
    llm.on("CriticOut", critic_pick())
    await chat.say("priya", "@yapper where is aman", mention=True)
    await chat.think()
    prompt = next(p for name, p in llm.calls if name == "Candidates")
    assert "5 min and shows up 45" in prompt
    assert "hospital" not in prompt
    assert "aman time" in prompt


async def test_opted_out_people_are_not_remembered(chat, rt, llm):
    await chat.say("aman", "!forget me")
    llm.on("Extraction", EXTRACTION)
    await memory.extract(rt, await _group_id())
    async with db.session() as ses:
        aman = await ses.scalar(select(Person).where(Person.wa_jid == PEOPLE["aman"]))
        mems = (await ses.scalars(select(Memory).where(Memory.person_id == aman.id))).all()
    assert mems == []
    assert aman.nicknames == []
