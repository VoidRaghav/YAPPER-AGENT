import asyncio

from tests.conftest import candidates, critic_pick, sent_texts


def slow(llm, schema_name: str, seconds: float = 5.0) -> None:
    inner = llm.parse

    async def parse(schema, **kwargs):
        if schema.__name__ == schema_name:
            await asyncio.sleep(seconds)
        return await inner(schema, **kwargs)

    llm.parse = parse


async def test_slow_critic_does_not_hold_up_a_reply_to_a_mention(chat, rt, llm, settings):
    settings.critic_timeout_seconds = 0.05
    llm.on("Candidates", candidates("trip ka plan 2023 se pending hai"))
    llm.on("CriticOut", critic_pick())
    slow(llm, "CriticOut")
    await chat.say("kabir", "yapper trip pe kaun aa raha hai", mention=True)
    result = await asyncio.wait_for(chat.think(), 2)
    assert sent_texts(rt) == ["trip ka plan 2023 se pending hai"]
    assert result["decision"]["reason"] == "addressed"


async def test_slow_critic_means_silence_for_unprompted_jokes(chat, rt, llm, settings):
    settings.critic_timeout_seconds = 0.05
    llm.on("GateOut", {"action": "REPLY", "confidence": 0.95, "target": "", "emoji": "", "mode": "banter", "reason": "setup"})
    llm.on("Candidates", candidates("kal se gym, as always"))
    llm.on("CriticOut", critic_pick())
    slow(llm, "CriticOut")
    await chat.say("aman", "kal se gym pakka")
    await asyncio.wait_for(chat.think(), 2)
    assert rt.gateway.sent == []


async def test_slow_mood_check_does_not_block_the_turn(chat, rt, llm, settings):
    settings.mood_timeout_seconds = 0.05
    llm.on("MoodOut", {"mood": "playful", "distressed_person": "", "reason": "x"})
    llm.on("Candidates", candidates("haan bolo"))
    llm.on("CriticOut", critic_pick())
    slow(llm, "MoodOut")
    await chat.say("kabir", "yapper hello", mention=True)
    result = await asyncio.wait_for(chat.think(), 2)
    assert result["mood"] == "neutral"
    assert sent_texts(rt) == ["haan bolo"]
