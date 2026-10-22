import pytest
from sqlalchemy import select

from app.db import Group, Memory, Person, db
from app.nodes.commands import DENIALS, help_text
from tests.conftest import PEOPLE, sent_texts

DENIAL_OPENERS = tuple(d.split("{owner}")[0] for d in DENIALS)


@pytest.fixture
def shared(settings):
    settings.shared_number = True
    settings.owner_name = "Kabir"
    return settings


async def _group() -> Group:
    async with db.session() as ses:
        return await ses.scalar(select(Group))


async def _person(who: str) -> Person:
    async with db.session() as ses:
        return await ses.scalar(select(Person).where(Person.wa_jid == PEOPLE[who]))


@pytest.mark.parametrize("command", ["!off", "!quiet 60", "!chatty high"])
async def test_friends_cannot_control_the_bot(chat, rt, shared, command):
    await chat.say("aman", command)
    group = await _group()
    assert group.enabled and group.quiet_until is None and group.chattiness == "med"
    reply = sent_texts(rt)[-1]
    assert reply.startswith(DENIAL_OPENERS) and "Kabir" in reply


async def test_owner_controls_the_bot(chat, rt, shared):
    await chat.say("kabir", "!off", from_owner=True)
    assert not (await _group()).enabled
    await chat.say("aman", "!on")
    assert not (await _group()).enabled
    await chat.say("kabir", "!on", from_owner=True)
    assert (await _group()).enabled


async def test_friend_on_while_off_stays_silent(chat, rt, shared):
    await chat.say("kabir", "!off", from_owner=True)
    sent = len(rt.gateway.sent)
    await chat.say("aman", "!on")
    assert len(rt.gateway.sent) == sent


async def test_owner_numbers_work_without_shared_mode(chat, rt, settings):
    settings.owner_numbers = "+91 33333 33333, 913333333333"
    await chat.say("priya", "!quiet 30")
    assert (await _group()).quiet_until is None
    await chat.say("rahul", "!quiet 30")
    assert (await _group()).quiet_until is not None


async def test_friends_keep_control_of_their_own_data(chat, rt, shared):
    await chat.say("aman", "hi")
    await chat.say("priya", "hello")
    aman, priya, group = await _person("aman"), await _person("priya"), await _group()
    async with db.session() as ses:
        ses.add(Memory(group_id=group.id, person_id=aman.id, text="aman supports rcb"))
        ses.add(Memory(group_id=group.id, person_id=priya.id, text="priya supports rcb too"))

    await chat.say("aman", "!forget rcb")
    async with db.session() as ses:
        left = (await ses.scalars(select(Memory.text))).all()
    assert left == ["priya supports rcb too"]

    await chat.say("aman", "!offlimits exams")
    assert (await _person("aman")).offlimits == ["exams"]

    await chat.say("aman", "!offlimits me")
    assert (await _person("aman")).roastable is False

    await chat.say("aman", "!forget me")
    assert (await _person("aman")).profile_json.get("opt_out") is True


async def test_group_wide_settings_and_other_peoples_files_are_owner_only(chat, rt, shared):
    await chat.say("aman", "hi")
    await chat.say("priya", "!offlimits group cricket")
    assert (await _group()).offlimits == []
    await chat.say("priya", "!whatdoyouknow @aman", mentions=[PEOPLE["aman"]])
    assert "dusron ki file" in sent_texts(rt)[-1]
    await chat.say("priya", "!whatdoyouknow me")
    assert "Priya" in sent_texts(rt)[-1]
    await chat.say("kabir", "!offlimits group cricket", from_owner=True)
    assert (await _group()).offlimits == ["cricket"]


async def test_fun_commands_stay_open_but_can_be_locked(chat, rt, shared):
    await chat.say("aman", "!stats")
    assert sent_texts(rt)[-1].startswith("this week")
    shared.owner_only_commands = "on,off,quiet,chatty,stats"
    await chat.say("aman", "!stats", gap=60)
    assert sent_texts(rt)[-1].startswith(DENIAL_OPENERS)


def test_help_marks_owner_only_commands(shared):
    text = help_text(shared)
    assert "!quiet [minutes] - main chup (sirf Kabir)" in text
    assert "!off / !on - band / chalu (sirf Kabir)" in text
    assert "!stats - is hafte ke stats" in text and "!stats - is hafte ke stats (sirf" not in text


async def test_intro_says_who_holds_the_remote(chat, rt, shared):
    shared.intro_on_add = True
    await chat.say("aman", "hello")
    intro = sent_texts(rt)[0]
    assert "on/off ka remote Kabir ke paas hai" in intro and "!off" not in intro


@pytest.mark.parametrize(
    ("jid", "owners", "expected"),
    [
        ("tg:123456789", "123456789", True),
        ("dc:998877665544332211", "998877665544332211", True),
        ("919876543210@s.whatsapp.net", "+91 98765 43210", True),
        ("tg:555", "123456789", False),
    ],
)
def test_owner_ids_work_across_platforms(settings, jid, owners, expected):
    from app.nodes.commands import is_owner
    from app.schemas import Event

    settings.owner_numbers = owners
    assert is_owner(settings, Event(type="message", group_jid="tg:-1", sender_jid=jid)) is expected
