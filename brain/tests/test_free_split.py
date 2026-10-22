import asyncio
import json
import random

import httpx

from app.config import Settings
from app.embeddings import Embedder
from app.gateway_client import DryRunGateway
from app.llm import LLMRouter, OpenAICompatLLM
from app.nodes.gate import GateOut
from app.nodes.roast import Candidates
from app.nodes.vision import ImageNote
from app.runtime import Runtime
from tests.conftest import GROUP, Chat, sent_texts
from tests.test_openai_compat import GATE, Server, client_for, reply

CANNED = {
    "Candidates": {"candidates": [{"text": "5 mins in aman time or actual time", "ref": "", "angle": "deadpan"}]},
    "CriticOut": {"reviews": [{"index": 0, "safe": True, "score": 8, "note": "lands"}], "best": 0},
    "MoodOut": {"mood": "playful", "distressed_person": "", "reason": "banter"},
    "GateOut": GATE,
}


def schema_name(body: dict) -> str:
    fmt = body.get("response_format", {})
    if fmt.get("type") == "json_schema":
        return fmt["json_schema"]["name"]
    system = body["messages"][0]["content"]
    return next(name for name, sample in CANNED.items() if all(f'"{k}"' in system for k in sample))


def free_settings(tmp_path, **extra) -> Settings:
    return Settings(
        _env_file=None, llm_provider="free", sarvam_api_key="sk_s", zai_api_key="z", llm_max_retries=0,
        db_url="sqlite+aiosqlite:///:memory:", data_dir=tmp_path, embed_provider="none", trends_enabled=False,
        intro_on_add=False, warmup_hours=0, mention_voice_chance=0, tts_provider="none", **extra,
    )


def test_free_preset_routes_writer_to_sarvam_and_rest_to_zai():
    s = Settings(_env_file=None, llm_provider="free")
    assert s.resolve(s.writer_model) == ("sarvam", "sarvam-105b")
    assert s.resolve(s.awards_model) == ("sarvam", "sarvam-105b")
    for model in (s.gate_model, s.mood_model, s.memory_model, s.critic_model, s.trends_model):
        assert s.resolve(model) == ("zai", "glm-4.5-flash")
    assert s.backup_model == "zai:glm-4.5-flash"
    assert s.image_understanding is False
    assert s.trends_scout_enabled is False
    assert s.missing_keys() == ["SARVAM_API_KEY", "ZAI_API_KEY"]


async def test_full_turn_on_free_split(database, tmp_path):
    sarvam = Server(lambda body, n: json.dumps(CANNED[schema_name(body)]))
    zai = Server(lambda body, n: json.dumps(CANNED[schema_name(body)]))
    s = free_settings(tmp_path)
    rt = Runtime(s, LLMRouter(s, {"sarvam": client_for(sarvam, s, "sarvam"), "zai": client_for(zai, s, "zai")}), DryRunGateway(s), Embedder(), random.Random(1))
    rt.allow_all, rt.immediate = True, True
    chat = Chat(rt)
    await chat.say("aman", "bro i'll reach in 5 mins")
    await chat.say("priya", "@yapper isko dekh", mention=True)
    await rt.think(GROUP, now=chat.now)
    assert sent_texts(rt) == ["5 mins in aman time or actual time"]

    writer = sarvam.bodies[0]
    assert writer["model"] == "sarvam-105b"
    assert "reasoning_effort" in writer and writer["reasoning_effort"] is None
    assert writer["max_tokens"] == 4096
    assert writer["temperature"] == 0.9
    assert writer["response_format"]["json_schema"]["name"] == "Candidates"

    assert {b["model"] for b in zai.bodies} == {"glm-4.5-flash"}
    assert all(b["response_format"] == {"type": "json_object"} for b in zai.bodies)
    assert all("thinking" not in b for b in zai.bodies)
    assert {schema_name(b) for b in zai.bodies} == {"MoodOut", "CriticOut"}
    await rt.stop()


async def test_sarvam_credits_exhausted_falls_back_to_free_glm(database, tmp_path):
    quota = httpx.Response(429, json={"error": {"code": "insufficient_quota_error", "message": "Credits exhausted"}})
    sarvam = Server(lambda body, n: quota)
    zai = Server(lambda body, n: json.dumps(CANNED[schema_name(body)]))
    s = free_settings(tmp_path)
    router = LLMRouter(s, {"sarvam": client_for(sarvam, s, "sarvam"), "zai": client_for(zai, s, "zai")})
    rt = Runtime(s, router, DryRunGateway(s), Embedder(), random.Random(1))
    rt.allow_all, rt.immediate = True, True
    chat = Chat(rt)
    await chat.say("priya", "@yapper bol kuch", mention=True)
    await rt.think(GROUP, now=chat.now)
    assert sent_texts(rt) == ["5 mins in aman time or actual time"]
    assert len(sarvam.bodies) == 1
    assert "sarvam" in router.down_until

    rt.busy_until.clear()
    await chat.say("priya", "@yapper aur bol", mention=True, gap=120)
    await rt.think(GROUP, now=chat.now)
    assert len(sarvam.bodies) == 1
    assert len(sent_texts(rt)) == 2
    await rt.stop()


async def test_truncated_reply_skips_repair_and_goes_to_backup(tmp_path):
    sarvam = Server(lambda body, n: httpx.Response(200, json=reply("", finish="length")))
    zai = Server(lambda body, n: json.dumps(CANNED["Candidates"]))
    s = free_settings(tmp_path)
    router = LLMRouter(s, {"sarvam": client_for(sarvam, s, "sarvam"), "zai": client_for(zai, s, "zai")})
    out = await router.parse(Candidates, model=s.writer_model, system="persona", prompt="write", creative=True)
    assert out.candidates[0].text.startswith("5 mins")
    assert len(sarvam.bodies) == 1
    assert len(zai.bodies) == 1


async def test_sarvam_reasoning_can_be_turned_back_on(tmp_path):
    sarvam = Server(lambda body, n: json.dumps(CANNED["Candidates"]))
    s = free_settings(tmp_path, sarvam_reasoning="low")
    await client_for(sarvam, s, "sarvam").parse(Candidates, model="sarvam-105b", system="s", prompt="p")
    assert sarvam.bodies[0]["reasoning_effort"] == "low"


async def test_backup_not_used_when_primary_works(tmp_path):
    sarvam = Server(lambda body, n: json.dumps(CANNED["Candidates"]))
    zai = Server(lambda body, n: json.dumps(CANNED["Candidates"]))
    s = free_settings(tmp_path)
    router = LLMRouter(s, {"sarvam": client_for(sarvam, s, "sarvam"), "zai": client_for(zai, s, "zai")})
    out = await router.parse(Candidates, model=s.writer_model, system="persona", prompt="write", creative=True)
    assert out.candidates[0].text.startswith("5 mins")
    assert zai.bodies == []


async def test_zai_vision_uses_raw_base64_and_no_response_format(tmp_path):
    zai = Server(lambda body, n: json.dumps({"description": "screenshot of a cricket meme"}))
    s = free_settings(tmp_path)
    router = LLMRouter(s, {"zai": client_for(zai, s, "zai")})
    out = await router.parse(ImageNote, model="zai:glm-4.6v-flash", system="s", prompt="describe", images=[("image/jpeg", "QUJD")], effort="low")
    assert out.description == "screenshot of a cricket meme"
    body = zai.bodies[0]
    assert body["model"] == "glm-4.6v-flash"
    assert "response_format" not in body
    assert body["messages"][1]["content"][0] == {"type": "image_url", "image_url": {"url": "QUJD"}}


async def test_zai_free_tier_runs_one_request_at_a_time(tmp_path):
    live = {"now": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.02)
        live["now"] -= 1
        return httpx.Response(200, json=reply(json.dumps(GATE)))

    s = free_settings(tmp_path)
    zai = OpenAICompatLLM(s, "zai", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    results = await asyncio.gather(*(zai.parse(GateOut, model="glm-4.7-flash", system="s", prompt="p") for _ in range(4)))
    assert all(r == GateOut(**GATE) for r in results)
    assert live["peak"] == 1

    live.update(now=0, peak=0)
    other = OpenAICompatLLM(s, "sarvam", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await asyncio.gather(*(other.parse(GateOut, model="sarvam-105b", system="s", prompt="p") for _ in range(4)))
    assert live["peak"] > 1


async def test_glm_47_flash_gets_thinking_disabled(tmp_path):
    zai = Server(lambda body, n: json.dumps(GATE))
    s = free_settings(tmp_path)
    await client_for(zai, s, "zai").parse(GateOut, model="glm-4.7-flash", system="s", prompt="p", effort="low")
    assert zai.bodies[0]["thinking"] == {"type": "disabled"}


async def test_glm_45_flash_gets_no_thinking_param(tmp_path):
    zai = Server(lambda body, n: json.dumps(GATE))
    s = free_settings(tmp_path)
    await client_for(zai, s, "zai").parse(GateOut, model="glm-4.5-flash", system="s", prompt="p")
    assert "thinking" not in zai.bodies[0]
