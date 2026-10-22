import asyncio
import importlib.util
import logging
from typing import Any

import numpy as np

from app.config import Settings

log = logging.getLogger(__name__)


def normalize(vectors: list[Any]) -> list[np.ndarray]:
    out = []
    for v in vectors:
        arr = np.asarray(v, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        out.append(arr / norm if norm else arr)
    return out


class Embedder:
    enabled = False

    async def embed(self, texts: list[str], kind: str = "document") -> list[np.ndarray] | None:
        return None

    async def one(self, text: str, kind: str = "query") -> np.ndarray | None:
        if not text.strip():
            return None
        out = await self.embed([text], kind)
        return out[0] if out else None


class FastEmbedder(Embedder):
    enabled = True

    def __init__(self, settings: Settings):
        self.s = settings
        self._model = None
        self._lock = asyncio.Lock()

    async def _load(self):
        async with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                cache = self.s.data_dir / "models"
                cache.mkdir(parents=True, exist_ok=True)
                self._model = await asyncio.to_thread(TextEmbedding, model_name=self.s.embed_model, cache_dir=str(cache))
        return self._model

    async def embed(self, texts: list[str], kind: str = "document") -> list[np.ndarray] | None:
        if not texts:
            return []
        try:
            model = await self._load()
            vectors = await asyncio.to_thread(lambda: list(model.embed(texts)))
            return normalize(vectors)
        except Exception as e:
            log.warning("fastembed failed: %s", e)
            return None


class VoyageEmbedder(Embedder):
    enabled = True

    def __init__(self, settings: Settings):
        import voyageai

        self.s = settings
        self.client = voyageai.AsyncClient(api_key=settings.voyage_api_key)

    async def embed(self, texts: list[str], kind: str = "document") -> list[np.ndarray] | None:
        if not texts:
            return []
        try:
            res = await self.client.embed(texts, model=self.s.voyage_model, input_type=kind, output_dimension=self.s.embed_dim)
            return normalize(res.embeddings)
        except Exception as e:
            log.warning("voyage failed: %s", e)
            return None


def make_embedder(settings: Settings) -> Embedder:
    if settings.embed_provider == "fastembed" and importlib.util.find_spec("fastembed"):
        return FastEmbedder(settings)
    if settings.embed_provider == "voyage" and settings.voyage_api_key and importlib.util.find_spec("voyageai"):
        return VoyageEmbedder(settings)
    if settings.embed_provider != "none":
        log.warning("embedding provider %s unavailable; recall falls back to keywords", settings.embed_provider)
    return Embedder()
