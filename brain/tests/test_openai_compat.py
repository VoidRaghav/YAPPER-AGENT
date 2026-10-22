import json
import random

import httpx
import pytest

from app.config import Settings
from app.embeddings import Embedder
from app.gateway_client import DryRunGateway
from app.llm import AnthropicLLM, LLMRouter, OpenAICompatLLM, extract_json, make_llm, strict_schema
from app.nodes.gate import GateOut
from app.nodes.roast import Candidates
from app.nodes.vision import ImageNote
from app.runtime import Runtime
from tests.conftest import GROUP, Chat, sent_texts

GATE = {"action": "REPLY", "confidence": 0.9, "target": "m1", "emoji": "", "mode": "banter", "reason": "setup"}


def reply(content: str, finish: str = "stop") -> dict:
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class Server:
    def __init__(self, responder) -> None:
        self.bodies: list[dict] = []
        self.responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        out = self.responder(body, len(self.bodies))
        return out if isinstance(out, httpx.Response) else httpx.Response(200, json=reply(out))


def client_for(server, settings: Settings, provider: str) -> OpenAICompatLLM:
    return OpenAICompatLLM(settings, provider, http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)))


def llm_with(server: Server, **overrides) -> OpenAICompatLLM:
    s = Settings(_env_file=None, llm_api_key="k", llm_max_retries=0, **overrides)
    return client_for(server, s, s.default_provider)


def test_factory_and_strict_schema():
    router = make_llm(Settings(_env_file=None))
    assert isinstance(router, LLMRouter) and isinstance(router.client("anthropic"), AnthropicLLM)
    assert isinstance(make_llm(Settings(_env_file=None, llm_provider="deepseek", llm_api_key="k")).client("deepseek"), OpenAICompatLLM)
    shape = strict_schema(Candidates)
    item = shape["$defs"]["Candidate"]
    assert shape["additionalProperties"] is False and shape["required"] == ["candidates"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"text", "ref", "angle"}
    assert "title" not in json.dumps(shape)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('<think>hmm let me see</think>\n{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('sure! here you go: {"a": {"b": 2}} hope it helps', '{"a": {"b": 2}}'),
    ],
)
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


async def test_openrouter_request_shape_and_thinking_output():
    server = Server(lambda body, n: "<think>planning the joke</think>```json\n" + json.dumps(GATE) + "\n```")
    llm = llm_with(server, llm_provider="openrouter")
    out = await llm.parse(GateOut, model="deepseek/deepseek-v4.1-flash", system="you gate", prompt="chat here", effort="low")
    assert out == GateOut(**GATE)
    body = server.bodies[0]
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"effort": "low", "exclude": True}
    assert body["temperature"] == 0.2
    assert body["messages"][0]["role"] == "system" and "JSON schema" in body["messages"][0]["content"]


async def test_deepseek_json_object_mode_and_creative_temperature():
    server = Server(lambda body, n: json.dumps({"candidates": [{"text": "5 mins in aman time", "ref": "", "angle": "deadpan"}]}))
    llm = llm_with(server, llm_provider="deepseek")
    out = await llm.parse(Candidates, model="deepseek-v4-pro", system="persona", prompt="write", creative=True)
    assert out.candidates[0].text == "5 mins in aman time"
    body = server.bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.9
    assert "provider" not in body and "reasoning" not in body and "reasoning_effort" not in body


async def test_gemini_maps_effort_to_reasoning_effort():
    server = Server(lambda body, n: json.dumps(GATE))
    llm = llm_with(server, llm_provider="gemini")
    await llm.parse(GateOut, model="gemini-3.1-flash-lite", system="s", prompt="p", effort="xhigh")
    assert server.bodies[0]["reasoning_effort"] == "high"
    assert server.bodies[0]["response_format"]["type"] == "json_schema"


async def test_invalid_json_gets_one_repair_retry():
    server = Server(lambda body, n: "not json at all" if n == 1 else json.dumps(GATE))
    llm = llm_with(server, llm_provider="deepseek")
    assert await llm.parse(GateOut, model="m", system="s", prompt="p") == GateOut(**GATE)
    assert len(server.bodies) == 2
    assert [m["role"] for m in server.bodies[1]["messages"]] == ["system", "user", "assistant", "user"]


async def test_gives_up_after_second_invalid_reply():
    server = Server(lambda body, n: '{"action": "MAYBE"}')
    llm = llm_with(server, llm_provider="deepseek")
    assert await llm.parse(GateOut, model="m", system="s", prompt="p") is None
    assert len(server.bodies) == 2


@pytest.mark.parametrize("status", [400, 429, 500])
async def test_api_errors_fail_safe(status):
    server = Server(lambda body, n: httpx.Response(status, json={"error": {"message": "nope"}}))
    llm = llm_with(server, llm_provider="openrouter")
    assert await llm.parse(GateOut, model="m", system="s", prompt="p") is None


async def test_images_are_sent_as_data_urls():
    server = Server(lambda body, n: json.dumps({"description": "a meme about mondays"}))
    llm = llm_with(server, llm_provider="openrouter")
    out = await llm.parse(ImageNote, model="m", system="s", prompt="describe", images=[("image/jpeg", "QUJD")])
    assert out.description == "a meme about mondays"
    parts = server.bodies[0]["messages"][1]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert parts[1] == {"type": "text", "text": "describe"}


async def test_research_uses_openrouter_web_plugin_only_when_enabled():
    server = Server(lambda body, n: "moye moye is trending again")
    off = llm_with(server, llm_provider="deepseek")
    assert await off.research(model="m", system="s", prompt="p") == ""
    assert server.bodies == []
    on = llm_with(server, llm_provider="openrouter")
    assert await on.research(model="m", system="s", prompt="p") == "moye moye is trending again"
    assert server.bodies[0]["plugins"] == [{"id": "web", "max_results": 8}]


async def test_full_turn_through_openai_compatible_provider(database, tmp_path):
    canned = {
        "Candidates": {"candidates": [{"text": "5 mins in aman time or actual time", "ref": "", "angle": "deadpan"}]},
        "CriticOut": {"reviews": [{"index": 0, "safe": True, "score": 8, "note": "lands"}], "best": 0},
        "MoodOut": {"mood": "playful", "distressed_person": "", "reason": "banter"},
    }
    server = Server(lambda body, n: json.dumps(canned[body["response_format"]["json_schema"]["name"]]))
    s = Settings(
        _env_file=None, llm_provider="openrouter", llm_api_key="k", db_url="sqlite+aiosqlite:///:memory:", data_dir=tmp_path, llm_max_retries=0,
        embed_provider="none", trends_enabled=False, intro_on_add=False, warmup_hours=0, mention_voice_chance=0, tts_provider="none",
    )
    llm = LLMRouter(s, {"openrouter": client_for(server, s, "openrouter")})
    rt = Runtime(s, llm, DryRunGateway(s), Embedder(), random.Random(3))
    rt.allow_all, rt.immediate = True, True
    chat = Chat(rt)
    await chat.say("aman", "bro i'll reach in 5 mins")
    await chat.say("priya", "@yapper isko dekh", mention=True)
    result = await rt.think(GROUP, now=chat.now)
    assert result["decision"]["reason"] == "addressed"
    assert sent_texts(rt) == ["5 mins in aman time or actual time"]
    models = {b["response_format"]["json_schema"]["name"]: b["model"] for b in server.bodies}
    assert models["Candidates"] == "z-ai/glm-5.3"
    assert models["CriticOut"] == "deepseek/deepseek-v4-pro"
    await rt.stop()
