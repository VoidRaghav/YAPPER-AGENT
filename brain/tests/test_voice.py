from types import SimpleNamespace

from app.config import Settings
from app.nodes import voice


class CapturingLLM:
    def __init__(self) -> None:
        self.models: list[str] = []

    async def parse(self, schema, *, model: str, **_):
        self.models.append(model)
        return schema(spoken="नमस्ते दोस्तों")


async def test_voice_script_conversion_uses_a_text_model_not_the_vision_model(tmp_path, monkeypatch):
    s = Settings(_env_file=None, llm_provider="free", tts_provider="edge", tts_script="devanagari", data_dir=tmp_path)
    llm = CapturingLLM()

    async def no_tts(*_):
        return False

    monkeypatch.setattr(voice, "_edge", no_tts)
    assert await voice.speak(SimpleNamespace(s=s, llm=llm), "namaste doston") is None
    assert llm.models == [s.mood_model]
    assert llm.models[0] != s.vision_model
