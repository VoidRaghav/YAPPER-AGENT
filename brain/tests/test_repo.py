from sqlalchemy import func, select

from app.db import BotLine, Group, Memory, Message, Person, db
from app.repo import purge_group
from tests.conftest import candidates, critic_pick


async def test_purge_group_removes_everything(chat, rt, llm):
    llm.on("Candidates", candidates("haan bolo"))
    llm.on("CriticOut", critic_pick())
    await chat.say("aman", "@yapper hello", mention=True)
    await chat.think()
    async with db.session() as ses:
        group = await ses.scalar(select(Group))
        ses.add(Memory(group_id=group.id, text="aman is always late"))
    async with db.session() as ses:
        await purge_group(ses, group.id)
    async with db.session() as ses:
        for model in (Group, Person, Message, Memory, BotLine):
            assert await ses.scalar(select(func.count(model.id))) == 0
