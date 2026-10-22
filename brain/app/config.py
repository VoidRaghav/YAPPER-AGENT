from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BRAIN_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BRAIN_DIR.parent

Chattiness = Literal["low", "med", "high"]
Provider = Literal["anthropic", "openrouter", "deepseek", "gemini", "sarvam", "zai", "groq", "free", "openai_compat"]


@dataclass(frozen=True)
class ProviderSpec:
    base_url: str
    key_field: str
    json_mode: str = "json_object"
    style: str = ""
    raw_base64_images: bool = False
    json_with_images: bool = True


PROVIDERS: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec("https://openrouter.ai/api/v1", "openrouter_api_key", "json_schema", "openrouter"),
    "deepseek": ProviderSpec("https://api.deepseek.com", "deepseek_api_key", "json_object"),
    "gemini": ProviderSpec("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini_api_key", "json_schema", "gemini"),
    "sarvam": ProviderSpec("https://api.sarvam.ai/v1", "sarvam_api_key", "json_schema", "sarvam"),
    "zai": ProviderSpec("https://api.z.ai/api/paas/v4/", "zai_api_key", "json_object", "zai", raw_base64_images=True, json_with_images=False),
    "groq": ProviderSpec("https://api.groq.com/openai/v1", "groq_api_key", "json_object", "groq"),
    "openai_compat": ProviderSpec("", "llm_api_key", "json_object"),
}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", **{name: spec.key_field.upper() for name, spec in PROVIDERS.items()}}


def _preset(writer: str, critic: str, fast: str, vision: str, **extra: object) -> dict[str, object]:
    return {
        "writer_model": writer,
        "awards_model": writer,
        "critic_model": critic,
        "gate_model": fast,
        "mood_model": fast,
        "memory_model": fast,
        "trends_model": fast,
        "scout_model": fast,
        "vision_model": vision,
        **extra,
    }


PRESETS: dict[str, dict[str, object]] = {
    "free": _preset(
        "sarvam:sarvam-105b", "zai:glm-4.5-flash", "zai:glm-4.5-flash", "zai:glm-4.6v-flash",
        backup_model="zai:glm-4.5-flash", image_understanding=False, trends_scout_enabled=False,
    ),
    "openrouter": _preset(
        "z-ai/glm-5.3", "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash",
        trends_scout_enabled=False,
    ),
    "deepseek": _preset("deepseek-v4-pro", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp", trends_scout_enabled=False),
    "gemini": _preset("gemini-3.8-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-3.1-flash-lite", trends_scout_enabled=False),
    "sarvam": _preset("sarvam-105b", "sarvam-105b", "sarvam-105b", "sarvam-105b", image_understanding=False, trends_scout_enabled=False),
    "zai": _preset("glm-4.7-flash", "glm-4.7-flash", "glm-4.7-flash", "glm-4.6v-flash", trends_scout_enabled=False),
    "groq": _preset("openai/gpt-oss-120b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "openai/gpt-oss-20b", image_understanding=False, trends_scout_enabled=False),
    "openai_compat": {"trends_scout_enabled": False},
}
DEFAULT_PROVIDER = {"free": "zai"}


def _split(value: str, sep: str = ",") -> list[str]:
    return [item.strip() for item in value.split(sep) if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT_DIR / ".env", BRAIN_DIR / ".env"), extra="ignore", env_ignore_empty=True)

    default_pack: str = "friends"
    packs_dir: Path = ROOT_DIR / "packs"
    extra_packs_dir: str = ""
    llm_provider: Provider = "anthropic"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_json_mode: Literal["json_schema", "json_object", "none"] = "json_schema"
    llm_deny_data_collection: bool = False
    backup_model: str = ""
    provider_cooldown_hours: float = 6
    openrouter_api_key: str = ""
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    sarvam_api_key: str = ""
    zai_api_key: str = ""
    groq_api_key: str = ""
    sarvam_max_tokens: int = 4096
    sarvam_reasoning: Literal["off", "low", "medium", "high"] = "off"
    zai_max_concurrency: int = 1
    creative_temperature: float = 0.9
    precise_temperature: float = 0.2
    anthropic_api_key: str = ""
    gate_model: str = "claude-haiku-5-5"
    mood_model: str = "claude-haiku-5-5"
    memory_model: str = "claude-haiku-5-5"
    vision_model: str = "claude-haiku-5-5"
    trends_model: str = "claude-haiku-5-5"
    critic_model: str = "claude-sonnet-5-5"
    writer_model: str = "claude-opus-5-5"
    awards_model: str = "claude-opus-5-5"
    scout_model: str = "claude-sonnet-5-5"
    writer_effort: str = "medium"
    critic_effort: str = "low"
    fast_effort: str = "low"
    llm_fallbacks: bool = True
    llm_timeout_seconds: float = 90
    llm_max_retries: int = 3

    bot_name: str = "Yapper"
    bot_aliases: str = ""
    allowed_group_jids: str = ""
    language_mix: str = "hinglish"
    timezone: str = "Asia/Kolkata"
    db_url: str = "sqlite:///./data/yapper.db"
    data_dir: Path = ROOT_DIR / "data"
    dry_run: bool = True
    gateway_url: str = "http://127.0.0.1:3001"
    internal_token: str = ""
    log_level: str = "INFO"

    gate_debounce_seconds: float = 6
    gate_mention_debounce_seconds: float = 1.5
    gate_max_wait_seconds: float = 20
    cooldown_window_minutes: int = 10
    cooldown_max_messages: int = 3
    min_gap_seconds: int = 60
    stale_minutes: int = 10
    hourly_cap_per_group: int = 12
    hourly_cap_global: int = 40
    mention_cap_per_window: int = 6
    warmup_hours: int = 48
    chattiness_default: Chattiness = "med"
    gate_threshold_low: float = 0.85
    gate_threshold_med: float = 0.72
    gate_threshold_high: float = 0.6
    gate_sample_low: float = 0.35
    gate_sample_med: float = 0.7
    gate_sample_high: float = 1.0
    react_daily_cap: int = 15
    voice_daily_cap: int = 1
    meme_daily_cap: int = 2
    mention_voice_chance: float = 0.08
    gate_messages: int = 20
    context_messages: int = 30
    mood_messages: int = 15
    mood_every_n: int = 4
    mood_ttl_minutes: int = 120
    serious_protect_hours: int = 12
    critic_min_score: int = 6
    critic_timeout_seconds: float = 25
    command_reply_cap: int = 5
    mood_timeout_seconds: float = 20
    recent_lines_buffer: int = 30
    repeat_similarity: float = 0.72
    max_reply_chars: int = 240
    max_reply_lines: int = 3
    reply_delay_min: float = 3
    reply_delay_max: float = 25
    typing_cps: float = 7
    quote_probability: float = 0.35
    allow_profanity: bool = False

    memory_every_n: int = 25
    memory_batch_max: int = 150
    memory_recall_k: int = 6
    lore_k: int = 4
    episodic_k: int = 3
    episodic_min_age_hours: int = 24

    embed_provider: Literal["fastembed", "voyage", "none"] = "fastembed"
    embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embed_dim: int = 384
    voyage_api_key: str = ""
    voyage_model: str = "voyage-3.5-lite"

    transcribe: bool = True
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    image_understanding: bool = True
    tts_provider: Literal["edge", "piper", "elevenlabs", "none"] = "edge"
    tts_voice: str = "hi-IN-MadhurNeural"
    tts_script: Literal["devanagari", "latin"] = "devanagari"
    tts_max_seconds: int = 10
    piper_model: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    ffmpeg_bin: str = "ffmpeg"

    meme_templates_dir: Path = ROOT_DIR / "assets" / "meme_templates"
    font_path: str = ""
    media_retention_days: int = 7

    awards_day: str = "sun"
    awards_hour: int = 20
    awards_minute: int = 0
    awards_min_messages: int = 20
    ghost_hours: float = 2

    trends_enabled: bool = True
    trends_refresh_hours: float = 6
    trends_scout_enabled: bool = True
    trends_scout_hours: float = 24
    trends_ttl_days: int = 10
    trends_k: int = 5
    trends_reuse_cooldown_hours: int = 72
    trends_allow_politics: bool = False
    trends_reddit_subs: str = "IndianDankMemes,indiameme,IndianMeyMeys,desimemes,bollywoodmemes,indiancricketmemes"
    trends_news_queries: str = (
        "viral meme india|trending on x india|linkedin post goes viral india|netizens react hilarious|"
        "bollywood viral video memes|cricket memes viral|shark tank india viral|bigg boss viral moment"
    )
    x_bearer_token: str = ""

    intro_on_add: bool = True
    backup_keep: int = 7
    shared_number: bool = False
    shared_bot_prefix: str = "🤖 "
    owner_name: str = ""
    owner_numbers: str = ""
    owner_only_commands: str = "on,off,quiet,chatty,vibe,spice"

    @model_validator(mode="after")
    def _apply_preset(self) -> "Settings":
        for key, value in PRESETS.get(self.llm_provider, {}).items():
            if key not in self.model_fields_set:
                object.__setattr__(self, key, value)
        return self

    @property
    def owner_known(self) -> bool:
        return self.shared_number or bool(_split(self.owner_numbers))

    @property
    def owner_number_set(self) -> set[str]:
        return {"".join(ch for ch in n if ch.isdigit()) for n in _split(self.owner_numbers)} - {""}

    @property
    def owner_only_set(self) -> set[str]:
        return {c.lower().lstrip("!") for c in _split(self.owner_only_commands)}

    @property
    def default_provider(self) -> str:
        return DEFAULT_PROVIDER.get(self.llm_provider, self.llm_provider)

    def resolve(self, model: str) -> tuple[str, str]:
        prefix, _, rest = model.partition(":")
        if rest and (prefix in PROVIDERS or prefix == "anthropic"):
            return prefix, rest
        return self.default_provider, model

    def api_key_for(self, provider: str) -> str:
        if provider == "anthropic":
            return self.anthropic_api_key
        specific = getattr(self, PROVIDERS[provider].key_field, "")
        return specific or (self.llm_api_key if provider == self.default_provider else "")

    def base_url_for(self, provider: str) -> str:
        if provider == self.default_provider and self.llm_base_url:
            return self.llm_base_url
        return PROVIDERS[provider].base_url

    def json_mode_for(self, provider: str) -> str:
        if provider == self.default_provider and "llm_json_mode" in self.model_fields_set:
            return self.llm_json_mode
        return PROVIDERS[provider].json_mode

    @property
    def role_models(self) -> list[str]:
        models = [self.gate_model, self.mood_model, self.memory_model, self.trends_model, self.critic_model, self.writer_model, self.awards_model]
        if self.image_understanding:
            models.append(self.vision_model)
        if self.trends_scout_enabled:
            models.append(self.scout_model)
        if self.backup_model:
            models.append(self.backup_model)
        return models

    def missing_keys(self) -> list[str]:
        providers = {self.resolve(m)[0] for m in self.role_models}
        return sorted(KEY_ENV[p] for p in providers if not self.api_key_for(p) and not (p == "openai_compat" and self.llm_base_url.startswith("http://")))

    @property
    def allowed_groups(self) -> set[str]:
        return set(_split(self.allowed_group_jids))

    @property
    def aliases(self) -> list[str]:
        return [a.lower() for a in [self.bot_name, *_split(self.bot_aliases)]]

    @property
    def reddit_subs(self) -> list[str]:
        return _split(self.trends_reddit_subs)

    @property
    def news_queries(self) -> list[str]:
        return _split(self.trends_news_queries, "|")

    @property
    def async_db_url(self) -> str:
        url = self.db_url
        if url.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
            path = url.split(":///", 1)[1]
            if path != ":memory:" and not Path(path).is_absolute():
                path = str((ROOT_DIR / path).resolve())
            return "sqlite+aiosqlite:///" + path
        if url.startswith(("postgres://", "postgresql://")):
            return "postgresql+asyncpg://" + url.split("://", 1)[1]
        return url

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def threshold(self, chattiness: str) -> float:
        return {"low": self.gate_threshold_low, "high": self.gate_threshold_high}.get(chattiness, self.gate_threshold_med)

    def sample_rate(self, chattiness: str) -> float:
        return {"low": self.gate_sample_low, "high": self.gate_sample_high}.get(chattiness, self.gate_sample_med)


@lru_cache
def get_settings() -> Settings:
    return Settings()
