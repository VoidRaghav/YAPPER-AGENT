from typing import Any, TypeVar

import numpy as np
from sqlalchemy import Float, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils import words

T = TypeVar("T")


def _pg_vector(vec: np.ndarray) -> Any:
    return literal_column("'[" + ",".join(f"{float(x):.6f}" for x in vec) + "]'::vector")


async def nearest(
    session: AsyncSession,
    model: type[T],
    vec: np.ndarray | None,
    filters: list[Any],
    k: int,
    scan_limit: int = 4000,
    min_score: float = 0.25,
) -> list[tuple[T, float]]:
    if vec is None or k <= 0:
        return []
    column = model.embedding
    if session.bind.dialect.name == "postgresql":
        dist = column.op("<=>", return_type=Float)(_pg_vector(vec))
        rows = (await session.execute(select(model, dist.label("d")).where(*filters, column.is_not(None)).order_by(dist).limit(k))).all()
        return [(obj, 1 - float(d)) for obj, d in rows if 1 - float(d) >= min_score]
    rows = (await session.scalars(select(model).where(*filters, column.is_not(None)).order_by(model.id.desc()).limit(scan_limit))).all()
    rows = [r for r in rows if r.embedding.shape == vec.shape]
    if not rows:
        return []
    matrix = np.stack([r.embedding for r in rows])
    scores = matrix @ vec
    order = np.argsort(-scores)[:k]
    return [(rows[i], float(scores[i])) for i in order if scores[i] >= min_score]


def keyword_rank(query: str, items: list[T], text_of, k: int) -> list[tuple[T, float]]:
    q = words(query)
    if not q:
        return []
    scored = []
    for item in items:
        overlap = len(q & words(text_of(item)))
        if overlap:
            scored.append((item, overlap / len(q)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]
