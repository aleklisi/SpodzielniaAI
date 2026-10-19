"""Topic clusters of the canonical corpus for the semantic detector (5.3).

Nightly (Celery beat, worker-core): spherical k-means (k-means++ seeding, cosine) over the
vectors of retrievable, non-honeypot canonical points, stored in Redis as one JSON document
``sem:clusters`` (``dim``, ``k``, float32 centroids in base64). Centroids are averages of many
blocks: they reveal no block text.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Protocol

import numpy as np

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from numpy.typing import NDArray
    from redis.asyncio import Redis

log = get_logger(__name__)

CLUSTERS_KEY = "sem:clusters"
type Matrix = NDArray[np.float32]


class VectorSource(Protocol):
    """``CanonicalVectorRepo``: retrievable, non-honeypot vectors in batches."""

    def active_vectors(self) -> AsyncIterator[list[list[float]]]: ...


def normalize(vectors: Matrix) -> Matrix:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.where(norms == 0, 1, norms)).astype(np.float32)


def _kmeans_once(points: Matrix, k: int, rng: np.random.Generator, iterations: int) -> Matrix:
    n = len(points)
    centroids = np.empty((k, points.shape[1]), dtype=np.float32)
    centroids[0] = points[rng.integers(n)]
    # k-means++: each next seed far (in cosine distance) from the chosen ones.
    closest = 1 - points @ centroids[0]
    for i in range(1, k):
        weights = np.clip(closest, 0, None) ** 2
        total = float(weights.sum())
        pick = rng.choice(n, p=weights / total) if total > 0 else int(rng.integers(n))
        centroids[i] = points[pick]
        closest = np.minimum(closest, 1 - points @ centroids[i])
    for _ in range(iterations):
        labels = np.argmax(points @ centroids.T, axis=1)
        updated = centroids.copy()
        for c in range(k):
            members = points[labels == c]
            if len(members):
                updated[c] = members.sum(axis=0)
        updated = normalize(updated)
        if np.allclose(updated, centroids, atol=1e-6):
            break
        centroids = updated
    return centroids


def kmeans(
    vectors: Matrix, k: int, *, seed: int = 0, iterations: int = 50, restarts: int = 8
) -> Matrix:
    """Spherical k-means: unit centroids maximising cosine similarity. The best of
    ``restarts`` runs (one run can merge two clusters and split another). ``k`` is capped at
    the number of vectors."""
    points = normalize(vectors)
    k = min(k, len(points))
    rng = np.random.default_rng(seed)
    best, best_fit = None, -np.inf
    for _ in range(restarts):
        centroids = _kmeans_once(points, k, rng, iterations)
        fit = float(np.max(points @ centroids.T, axis=1).sum())
        if fit > best_fit:
            best, best_fit = centroids, fit
    assert best is not None  # noqa: S101 - restarts >= 1
    return best


def nearest(centroids: Matrix, vectors: Matrix) -> NDArray[np.int64]:
    return np.argmax(normalize(vectors) @ centroids.T, axis=1)


def encode(centroids: Matrix) -> str:
    k, dim = centroids.shape
    data = base64.b64encode(centroids.astype(np.float32).tobytes()).decode()
    return json.dumps({"k": k, "dim": dim, "data": data})


def decode(raw: str | bytes) -> Matrix:
    doc = json.loads(raw)
    flat = np.frombuffer(base64.b64decode(doc["data"]), dtype=np.float32)
    return flat.reshape(int(doc["k"]), int(doc["dim"]))


async def load_clusters(redis: Redis) -> Matrix | None:
    raw = await redis.get(CLUSTERS_KEY)
    return decode(raw) if raw is not None else None


async def store_clusters(redis: Redis, centroids: Matrix) -> None:
    await redis.set(CLUSTERS_KEY, encode(centroids))


async def refresh_clusters(repo: VectorSource, redis: Redis, k: int) -> int:
    """Recompute and store the centroids. Returns k (0: no vectors, the old ones are kept)."""
    vectors = [v async for batch in repo.active_vectors() for v in batch]
    if not vectors:
        log.info("shield.clusters_skipped", reason="no_vectors")
        return 0
    centroids = kmeans(np.asarray(vectors, dtype=np.float32), k)
    await store_clusters(redis, centroids)
    log.info("shield.clusters_refreshed", k=len(centroids), points=len(vectors))
    return len(centroids)
