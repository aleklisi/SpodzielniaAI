"""``spoldzielnia atlas benchmark``: how well the visual index recognises labels (chunk 9.8).

Leave-one-out over the marks already in ``atlas_visual_index``: each region's vector is matched
against the regions of *other* photos; the label of the nearest ones is the guess. Reports
top-1/top-3 label accuracy and the score distribution of right and wrong top-1 matches, and
suggests the lowest score threshold at which top-1 guesses are right at least 90 % of the time
(``atlas.recognition_min_score``). Synthetic pictures say nothing about real ones: run this on
the cooperative's own approved photos.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from spoldzielnia_ai.atlas.vectors import active_atlas_conditions
from spoldzielnia_ai.vector.collections import ATLAS_COLLECTION

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

TARGET_PRECISION = 0.9


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    regions: int
    photos: int
    labels: int
    top1: float | None
    top3: float | None
    right_mean: float | None
    wrong_mean: float | None
    suggested_min_score: float | None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


async def _regions(client: AsyncQdrantClient) -> list[tuple[str, str, list[float]]]:
    from qdrant_client import models

    rows: list[tuple[str, str, list[float]]] = []
    offset: Any = None
    while True:
        records, offset = await client.scroll(
            ATLAS_COLLECTION,
            scroll_filter=models.Filter(
                must=[
                    *active_atlas_conditions(),
                    models.FieldCondition(key="kind", match=models.MatchValue(value="region")),
                ]
            ),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for record in records:
            payload = record.payload or {}
            vector: Any = record.vector  # a single dense vector in this collection
            if isinstance(vector, list) and payload.get("label_id"):
                dense = [float(v) for v in vector]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
                rows.append((str(payload["atlas_image_id"]), str(payload["label_id"]), dense))
        if offset is None:
            return rows


def evaluate(rows: list[tuple[str, str, list[float]]]) -> BenchmarkReport:
    photos = {r[0] for r in rows}
    labels = {r[1] for r in rows}
    if len(photos) < 2:
        return BenchmarkReport(len(rows), len(photos), len(labels), None, None, None, None, None)
    vectors = np.array([r[2] for r in rows], dtype=np.float64)
    sims = vectors @ vectors.T
    image_of = np.array([r[0] for r in rows])
    label_of = np.array([r[1] for r in rows])
    top1 = top3 = 0
    evaluated = 0
    scored: list[tuple[float, bool]] = []
    for i in range(len(rows)):
        others = np.flatnonzero(image_of != image_of[i])
        if others.size == 0:
            continue
        order = others[np.argsort(-sims[i, others])]
        evaluated += 1
        right = bool(label_of[order[0]] == label_of[i])
        top1 += right
        top3 += bool(label_of[i] in set(label_of[order[:3]]))
        scored.append((float(sims[i, order[0]]), right))
    right_scores = [s for s, ok in scored if ok]
    wrong_scores = [s for s, ok in scored if not ok]
    suggested = None
    for threshold in sorted({round(s, 3) for s, _ in scored}):
        kept = [ok for s, ok in scored if s >= threshold]
        if kept and sum(kept) / len(kept) >= TARGET_PRECISION:
            suggested = threshold
            break
    return BenchmarkReport(
        regions=len(rows),
        photos=len(photos),
        labels=len(labels),
        top1=round(top1 / evaluated, 4) if evaluated else None,
        top3=round(top3 / evaluated, 4) if evaluated else None,
        right_mean=round(float(np.mean(right_scores)), 4) if right_scores else None,
        wrong_mean=round(float(np.mean(wrong_scores)), 4) if wrong_scores else None,
        suggested_min_score=suggested,
    )


async def run_benchmark(client: AsyncQdrantClient) -> BenchmarkReport:
    return evaluate(await _regions(client))
