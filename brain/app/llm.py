import asyncio
import copy
import json
import logging
import re
import time
from typing import Any, TypeVar

import anthropic
import openai
from pydantic import BaseModel, ValidationError

from app.config import PROVIDERS, Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
WEB_SEARCH_TOOL = "web_search_20260209"
THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class AnthropicLLM:
    def __init__(self, settings: Settings):
        self.s = settings
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            max_retries=3,
            timeout=settings.llm_timeout_seconds,
        )

    def _common(self, model: str, system: str, effort: str | None, cache: bool) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "text", "text": system}
        if cache:
            block["cache_control"] = {"type": "ephemeral"}
        kwargs: dict[str, Any] = {"model": model, "system": [block]}
        if effort:
            kwargs["output_config"] = {"effort": effort}
        if self.s.llm_fallbacks and model.startswith(FALLBACK_MODELS):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    @staticmethod
    def _content(prompt: str, images: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}} for mt, data in images or []
        ]
        blocks.append({"type": "text", "text": prompt})
        return blocks

    @staticmethod
    def _log_usage(tag: str, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if u:
            log.debug(
                "%s model=%s in=%s out=%s cache_read=%s",
                tag,
                getattr(resp, "model", "?"),
                u.input_tokens,
                u.output_tokens,
                getattr(u, "cache_read_input_tokens", 0),
            )

    async def parse(
        self,
        schema: type[T],
        *,
        model: str,
        system: str,
        prompt: str,
        effort: str | None = None,
        max_tokens: int = 4000,
        images: list[tuple[str, str]] | None = None,
        cache: bool = True,
        creative: bool = False,
    ) -> T | None:
        try:
            resp = await self.client.beta.messages.parse(
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": self._content(prompt, images)}],
                output_format=schema,
                **self._common(model, system, effort, cache),
            )
        except anthropic.BadRequestError as e:
            log.error("llm bad request (%s): %s", model, e.message)
            return None
        except anthropic.RateLimitError:
            log.warning("llm rate limited (%s)", model)
            return None
        except anthropic.APIStatusError as e:
            log.warning("llm api error %s (%s): %s", e.status_code, model, e.message)
            return None
        except anthropic.APIConnectionError as e:
            log.warning("llm connection error (%s): %s", model, e)
            return None
        except (ValueError, anthropic.AnthropicError) as e:
            log.warning("llm parse failed (%s): %s", model, e)
            return None
        self._log_usage(schema.__name__, resp)
        if resp.stop_reason == "refusal":
            log.info("llm refused (%s): %s", model, getattr(resp.stop_details, "category", None))
            return None
        return resp.parsed_output

    async def research(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        max_searches: int = 8,
        effort: str | None = "medium",
        max_tokens: int = 16000,
        max_continuations: int = 4,
    ) -> str:
        tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches, "user_location": {"type": "approximate", "country": "IN", "timezone": self.s.timezone}}]
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        resp = None
        try:
            for _ in range(max_continuations + 1):
                resp = await self.client.beta.messages.create(
                    max_tokens=max_tokens,
                    messages=messages,
                    tools=tools,
                    **self._common(model, system, effort, cache=False),
                )
                self._log_usage("research", resp)
                if resp.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": resp.content})
        except anthropic.AnthropicError as e:
            log.warning("research failed: %s", e)
            return ""
        if not resp or resp.stop_reason == "refusal":
            return ""
        return "\n".join(b.text for b in resp.content if b.type == "text").strip()


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    root = copy.deepcopy(schema.model_json_schema())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(root)
    return root


def extract_json(text: str) -> str:
    t = THINK.sub("", text or "").strip()
    t = FENCE.sub("", t).strip()
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


QUOTA_CODES = {"insufficient_quota", "insufficient_quota_error", "1113"}


class OpenAICompatLLM:
    def __init__(self, settings: Settings, provider: str, http_client: Any = None):
        self.s = settings
        self.provider = provider
        self.spec = PROVIDERS[provider]
        self.json_mode = settings.json_mode_for(provider)
        self.quota_exhausted = False
        limit = settings.zai_max_concurrency if provider == "zai" else 0
        self.gate = asyncio.Semaphore(limit) if limit > 0 else None
        self.client = openai.AsyncOpenAI(
            api_key=settings.api_key_for(provider) or "not-needed",
            base_url=settings.base_url_for(provider) or None,
            max_retries=settings.llm_max_retries,
            timeout=settings.llm_timeout_seconds,
            default_headers={"X-Title": settings.bot_name} if provider == "openrouter" else None,
            http_client=http_client,
        )

    def _provider_params(self, model: str, effort: str | None) -> dict[str, Any]:
        level = {"xhigh": "high", "max": "high"}.get(effort or "", effort)
        style = self.spec.style
        if style == "gemini":
            return {"reasoning_effort": level} if level else {}
        if style == "sarvam":
            mode = self.s.sarvam_reasoning
            return {"extra_body": {"reasoning_effort": None if mode == "off" else mode}}
        if style == "groq":
            return {"reasoning_effort": level} if level and "gpt-oss" in model else {}
        if style == "zai":
            if model.startswith("glm-4.5"):
                return {}
            mode = "enabled" if level in ("medium", "high") else "disabled"
            return {"extra_body": {"thinking": {"type": mode}}}
        if style == "openrouter":
            provider: dict[str, Any] = {"require_parameters": self.json_mode == "json_schema"}
            if self.s.llm_deny_data_collection:
                provider["data_collection"] = "deny"
            body: dict[str, Any] = {"provider": provider}
            if level:
                body["reasoning"] = {"effort": level, "exclude": True}
            return {"extra_body": body}
        return {}

    def _user_content(self, prompt: str, images: list[tuple[str, str]] | None) -> Any:
        if not images:
            return prompt
        parts: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": data if self.spec.raw_base64_images else f"data:{mt};base64,{data}"}} for mt, data in images
        ]
        parts.append({"type": "text", "text": prompt})
        return parts

    def _response_format(self, schema: type[BaseModel], shape: dict[str, Any], images: bool) -> dict[str, Any] | None:
        if images and not self.spec.json_with_images:
            return None
        if self.json_mode == "json_schema":
            return {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": shape, "strict": True}}
        if self.json_mode == "json_object":
            return {"type": "json_object"}
        return None

    def _note_error(self, e: openai.APIStatusError) -> None:
        code = str(getattr(e, "code", "") or "")
        text = str(e.message).lower()
        if code in QUOTA_CODES or "insufficient balance" in text or "credits exhausted" in text or "insufficient_quota" in text:
            self.quota_exhausted = True

    async def _complete(
        self, model: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, response_format: dict | None, effort: str | None
    ) -> tuple[str | None, bool]:
        if self.spec.style == "sarvam":
            max_tokens = min(max_tokens, self.s.sarvam_max_tokens)
        if self.spec.style == "zai":
            temperature = min(temperature, 1.0)
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if response_format:
            kwargs["response_format"] = response_format
        kwargs.update(self._provider_params(model, effort))
        try:
            if self.gate:
                async with self.gate:
                    resp = await self.client.chat.completions.create(**kwargs)
            else:
                resp = await self.client.chat.completions.create(**kwargs)
        except openai.APIStatusError as e:
            self._note_error(e)
            log.warning("llm api error %s (%s:%s): %s", e.status_code, self.provider, model, str(e.message)[:300])
            return None, False
        except openai.APIConnectionError as e:
            log.warning("llm connection error (%s:%s): %s", self.provider, model, e)
            return None, False
        except openai.OpenAIError as e:
            log.warning("llm failed (%s:%s): %s", self.provider, model, e)
            return None, False
        if not resp.choices:
            log.warning("llm returned no choices (%s:%s)", self.provider, model)
            return None, False
        if resp.usage:
            log.debug("llm %s:%s in=%s out=%s", self.provider, model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
        choice = resp.choices[0]
        truncated = choice.finish_reason == "length"
        if truncated:
            log.warning("llm hit max_tokens (%s:%s)", self.provider, model)
        return choice.message.content or "", truncated

    async def parse(
        self,
        schema: type[T],
        *,
        model: str,
        system: str,
        prompt: str,
        effort: str | None = None,
        max_tokens: int = 4000,
        images: list[tuple[str, str]] | None = None,
        cache: bool = True,
        creative: bool = False,
    ) -> T | None:
        shape = strict_schema(schema)
        instructions = (
            f"{system}\n\nReply with one JSON object only, no prose and no code fences. "
            f"It must match this JSON schema exactly:\n{json.dumps(shape, ensure_ascii=False)}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": self._user_content(prompt, images)},
        ]
        temperature = self.s.creative_temperature if creative else self.s.precise_temperature
        fmt = self._response_format(schema, shape, bool(images))
        for attempt in range(2):
            text, truncated = await self._complete(model, messages, max_tokens, temperature, fmt, effort)
            if text is None:
                return None
            try:
                return schema.model_validate_json(extract_json(text))
            except (ValidationError, ValueError) as e:
                if attempt or truncated:
                    log.warning("llm returned invalid %s (%s:%s): %s", schema.__name__, self.provider, model, str(e)[:200])
                    return None
                messages += [
                    {"role": "assistant", "content": text[:4000]},
                    {"role": "user", "content": f"That was not valid JSON for the schema ({str(e)[:300]}). Reply again with only the corrected JSON object."},
                ]
        return None

    async def research(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        max_searches: int = 8,
        effort: str | None = "medium",
        max_tokens: int = 8000,
        max_continuations: int = 0,
    ) -> str:
        if self.spec.style != "openrouter":
            return ""
        try:
            resp = await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                extra_body={"plugins": [{"id": "web", "max_results": max_searches}]},
            )
        except openai.OpenAIError as e:
            log.warning("research failed: %s", e)
            return ""
        return THINK.sub("", resp.choices[0].message.content or "").strip() if resp.choices else ""


class LLMRouter:
    def __init__(self, settings: Settings, clients: dict[str, Any] | None = None):
        self.s = settings
        self.clients: dict[str, Any] = dict(clients or {})
        self.down_until: dict[str, float] = {}

    def client(self, provider: str) -> Any:
        if provider not in self.clients:
            self.clients[provider] = AnthropicLLM(self.s) if provider == "anthropic" else OpenAICompatLLM(self.s, provider)
        return self.clients[provider]

    def _available(self, provider: str) -> bool:
        return self.down_until.get(provider, 0) <= time.monotonic()

    async def _try(self, schema: type[T], model: str, kwargs: dict[str, Any]) -> T | None:
        provider, bare = self.s.resolve(model)
        if not self._available(provider):
            return None
        client = self.client(provider)
        out = await client.parse(schema, model=bare, **kwargs)
        if out is None and getattr(client, "quota_exhausted", False):
            client.quota_exhausted = False
            self.down_until[provider] = time.monotonic() + self.s.provider_cooldown_hours * 3600
            log.warning("%s credits exhausted; using backup model for %.0fh", provider, self.s.provider_cooldown_hours)
        return out

    async def parse(self, schema: type[T], *, model: str, **kwargs: Any) -> T | None:
        out = await self._try(schema, model, kwargs)
        backup = self.s.backup_model
        if out is None and backup and backup != model:
            out = await self._try(schema, backup, kwargs)
        return out

    async def research(self, *, model: str, **kwargs: Any) -> str:
        provider, bare = self.s.resolve(model)
        return await self.client(provider).research(model=bare, **kwargs)


async def bounded(coro: Any, seconds: float) -> Any:
    if seconds <= 0:
        return await coro
    try:
        return await asyncio.wait_for(coro, seconds)
    except TimeoutError:
        log.info("llm call gave up after %.0fs", seconds)
        return None


def make_llm(settings: Settings) -> LLMRouter:
    return LLMRouter(settings)
