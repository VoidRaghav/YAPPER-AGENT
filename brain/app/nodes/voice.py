import asyncio
import importlib.util
import logging
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from app import prompts
from app.config import Settings

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

_whisper = None
_whisper_lock = asyncio.Lock()


class SpokenLine(BaseModel):
    spoken: str


async def _load_whisper(s: Settings):
    global _whisper
    async with _whisper_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel

            root = s.data_dir / "models" / "whisper"
            root.mkdir(parents=True, exist_ok=True)
            _whisper = await asyncio.to_thread(
                WhisperModel, s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type, download_root=str(root)
            )
    return _whisper


async def transcribe(rt: "Runtime", audio: bytes) -> str | None:
    s = rt.s
    if not s.transcribe or not audio or not importlib.util.find_spec("faster_whisper"):
        return None
    try:
        model = await _load_whisper(s)
    except Exception as e:
        log.warning("whisper unavailable: %s", e)
        return None

    def run() -> str:
        with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
            f.write(audio)
            f.flush()
            segments, _ = model.transcribe(f.name, beam_size=1, vad_filter=True)
            return " ".join(seg.text.strip() for seg in segments).strip()

    try:
        return await asyncio.to_thread(run) or None
    except Exception as e:
        log.warning("transcription failed: %s", e)
        return None


async def _ffmpeg_to_ogg(s: Settings, src: Path, dst: Path) -> bool:
    if not shutil.which(s.ffmpeg_bin):
        log.warning("ffmpeg not found; voice replies disabled")
        return False
    proc = await asyncio.create_subprocess_exec(
        s.ffmpeg_bin, "-y", "-loglevel", "error", "-i", str(src), "-t", str(s.tts_max_seconds),
        "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "32k", "-application", "voip", str(dst),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        log.warning("ffmpeg failed: %s", err.decode(errors="ignore")[:300])
    return proc.returncode == 0 and dst.exists()


async def _edge(s: Settings, text: str, out: Path) -> bool:
    import edge_tts

    await edge_tts.Communicate(text, s.tts_voice, rate="+6%").save(str(out))
    return out.exists() and out.stat().st_size > 0


async def _piper(s: Settings, text: str, out: Path) -> bool:
    if not s.piper_model or not shutil.which("piper"):
        return False
    proc = await asyncio.create_subprocess_exec("piper", "--model", s.piper_model, "--output_file", str(out), stdin=asyncio.subprocess.PIPE)
    await proc.communicate(text.encode())
    return proc.returncode == 0 and out.exists()


async def _elevenlabs(s: Settings, text: str, out: Path) -> bool:
    if not (s.elevenlabs_api_key and s.elevenlabs_voice_id):
        return False
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}",
            headers={"xi-api-key": s.elevenlabs_api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": s.elevenlabs_model},
        )
    if res.status_code != 200:
        log.warning("elevenlabs failed: %s", res.status_code)
        return False
    out.write_bytes(res.content)
    return True


async def speak(rt: "Runtime", text: str) -> bytes | None:
    s = rt.s
    if s.tts_provider == "none" or not text.strip():
        return None
    spoken = text
    if s.tts_script == "devanagari":
        out = await rt.llm.parse(SpokenLine, model=s.mood_model, system=prompts.render("voice"), prompt=text, effort=s.fast_effort, max_tokens=800)
        if out and out.spoken.strip():
            spoken = out.spoken.strip()
    synth = {"edge": _edge, "piper": _piper, "elevenlabs": _elevenlabs}[s.tts_provider]
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / ("raw.wav" if s.tts_provider == "piper" else "raw.mp3")
        ogg = Path(tmp) / "voice.ogg"
        try:
            if not await synth(s, spoken, raw):
                return None
        except Exception as e:
            log.warning("tts failed: %s", e)
            return None
        if not await _ffmpeg_to_ogg(s, raw, ogg):
            return None
        return ogg.read_bytes()
