import pytest
from sqlalchemy import func, select

from app.db import Group, Memory, Message, Person, db
from app.nodes.commands import parse
from app.schemas import Quoted
from tests.conftest import PEOPLE, candidates, critic_pick, sent_texts


@pytest.mark.parametrize(
    ("text", "name", "args"),
    [
        ("!quiet 30", "quiet", "30"),
        ("!QUIET", "quiet", ""),
        ("@919999999999 !roast @aman", "roast", "@aman"),
        ("  !chatty high", "chatty", "high"),
        ("!forget me", "forget", "me"),
        ("!offlimits group exams", "offlimits", "group exams"),
    ],
)
def test_parse(text, name, args):
    cmd = parse(text)
    assert cmd and cmd.name == name and cmd.args == args


@pytest.mark.parametrize("text", ["hello !quiet", "!notacommand", "", "quiet 30"])
def test_parse_rejects(text):
    assert parse(text) is None


async def _group() -> Group:
    async with db.session() as ses:
        return await ses.scalar(select(Group))


async def _person(who: str) -> Person:
    async with db.session() as ses:
        return await ses.scalar(select(Person).where(Person.wa_jid == PEOPLE[who]))


async def test_quiet_sets_time_and_acks(chat, rt):
    await chat.say("aman", "!quiet 15")
    group = await _group()
    assert group.quiet_until is not None
    assert (group.quiet_until - chat.now).total_seconds() == pytest.approx(900, abs=1)
    assert "15" in sent_texts(rt)[-1]
    assert rt.gateway.sent[-1]["delay_ms"] < 5000


async def test_chatty(chat):
    await chat.say("aman", "!chatty high")
    assert (await _group()).chattiness == "high"
    await chat.say("aman", "!chatty banana")
    assert (await _group()).chattiness == "high"


async def test_off_stops_storing_and_on_resumes(chat, rt):
    await chat.say("aman", "!off")
    assert not (await _group()).enabled
    await chat.say("priya", "secret stuff while bot is off")
    async with db.session() as ses:
        texts = (await ses.scalars(select(Message.text).where(Message.from_bot.is_(False)))).all()
    assert "secret stuff while bot is off" not in texts
    await chat.say("priya", "!quiet 5")
    assert (await _group()).quiet_until is None
    await chat.say("aman", "!on")
    assert (await _group()).enabled


async def test_offlimits_variants(chat):
    await chat.say("aman", "!offlimits my job")
    assert (await _person("aman")).offlimits == ["my job"]
    await chat.say("aman", "!offlimits me")
    assert (await _person("aman")).roastable is False
    await chat.say("aman", "!offlimits group cricket")
    assert (await _group()).offlimits == ["cricket"]
    await chat.say("aman", "!offlimits remove cricket")
    assert (await _group()).offlimits == []


async def test_forget_me_and_forget_text_and_dossier(chat, rt):
    await chat.say("aman", "hi")
    aman, group = await _person("aman"), await _group()
    async with db.session() as ses:
        ses.add(Memory(group_id=group.id, person_id=aman.id, kind="habit", text="aman always says 5 min and takes 45"))
        ses.add(Memory(group_id=group.id, person_id=aman.id, kind="fact", text="aman supports rcb"))
    await chat.say("priya", "!whatdoyouknow @aman", mentions=[PEOPLE["aman"]])
    assert "aman supports rcb" in sent_texts(rt)[-1]
    await chat.say("priya", "!forget rcb")
    assert "rcb" in sent_texts(rt)[-1]
    await chat.say("aman", "!forget me")
    async with db.session() as ses:
        assert await ses.scalar(select(func.count(Memory.id))) == 0
    assert (await _person("aman")).profile_json.get("opt_out") is True
    await chat.say("aman", "!remember me")
    assert not (await _person("aman")).profile_json.get("opt_out")


async def test_roast_command_runs_forced_turn(chat, rt, llm):
    llm.on("Candidates", candidates("aman ka 5 min is a unit of geological time"))
    llm.on("CriticOut", critic_pick())
    await chat.say("aman", "hello")
    await chat.say("priya", "!roast @aman", mentions=[PEOPLE["aman"]])
    assert sent_texts(rt)[-1] == "aman ka 5 min is a unit of geological time"
    _, prompt = next(c for c in llm.calls if c[0] == "Candidates")
    assert "Roast request aimed at: Aman" in prompt


async def test_roast_respects_offlimits_me(chat, rt, llm):
    await chat.say("aman", "!offlimits me")
    await chat.say("priya", "!roast aman")
    assert "protected" in sent_texts(rt)[-1]
    assert llm.called("Candidates") == 0


async def test_stats_command(chat, rt):
    for _ in range(3):
        await chat.say("aman", "yap yap")
    await chat.say("priya", "!stats")
    assert "biggest yapper: Aman" in sent_texts(rt)[-1]


async def test_sticker_from_quoted_text(chat, rt):
    mid = await chat.say("rahul", "main kal se gym jaunga pakka")
    async with db.session() as ses:
        wa_id = (await ses.get(Message, mid)).wa_id
    await chat.say("aman", "!sticker", quoted=Quoted(id=wa_id, participant=PEOPLE["rahul"], text="main kal se gym jaunga pakka"))
    assert rt.gateway.sent[-1]["kind"] == "sticker"
    assert len(rt.gateway.sent[-1]["media_b64"]) > 1000


async def test_help(chat, rt):
    await chat.say("aman", "!help")
    assert "!quiet" in sent_texts(rt)[-1]
