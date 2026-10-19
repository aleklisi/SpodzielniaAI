"""Chunk 5.3: ``SemanticAnomalyDetector`` and the topic clusters behind it.

Synthetic sessions in a toy 32-dim space with 20 clusters (fixed seed): a beekeeper asking
around one or two topics, and enumerators walking the corpus one cluster per query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.clusters import (
    decode,
    encode,
    kmeans,
    load_clusters,
    nearest,
    refresh_clusters,
    store_clusters,
)
from spoldzielnia_ai.shield.semantic import (
    SemanticAnomalyDetector,
    SemanticVerdict,
    jaccard,
    tokens,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from numpy.typing import NDArray

DIM, CLUSTERS = 32, 20
RNG_SEED = 1234

BEEKEEPER_QUESTIONS = [
    "Jak leczyć warrozę zimą kwasem szczawiowym?",
    "Kiedy najlepiej odymiać rodziny przeciw warrozie?",
    "Czy amitraz jest bezpieczny dla czerwiu?",
    "Ile osypu roztoczy na wkładce to dużo w październiku?",
    "Jaką dawkę kwasu mrówkowego stosować latem?",
    "Czy można leczyć rodzinę podczas pożytku?",
    "Co zrobić, gdy po zabiegu osyp jest nadal wysoki?",
    "Jak przygotować syrop z kwasem szczawiowym?",
]


class Space:
    def __init__(self, seed: int = RNG_SEED) -> None:
        self.rng = np.random.default_rng(seed)
        centroids = self.rng.normal(size=(CLUSTERS, DIM))
        self.centroids = (centroids / np.linalg.norm(centroids, axis=1, keepdims=True)).astype(
            np.float32
        )

    def point(self, cluster: int, noise: float = 0.05) -> list[float]:
        v = self.centroids[cluster] + self.rng.normal(scale=noise, size=DIM)
        return [float(x) for x in v / np.linalg.norm(v)]


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


async def _detector(space: Space | None = None) -> tuple[SemanticAnomalyDetector, Clock]:
    redis = FakeAsyncRedis()
    if space is not None:
        await store_clusters(redis, space.centroids)
    clock = Clock()
    return SemanticAnomalyDetector(redis, ShieldSettings(), clock), clock


async def test_beekeeper_session_stays_low() -> None:
    space = Space()
    detector, clock = await _detector(space)
    user = uuid4()
    topics = [3] * 8 + [7] * 4 + [3] * 10 + [7] * 8  # two topics, in runs
    verdicts: list[SemanticVerdict] = []
    for i, cluster in enumerate(topics):
        clock.now += 240  # one question every 4 minutes
        question = BEEKEEPER_QUESTIONS[i % len(BEEKEEPER_QUESTIONS)]
        verdicts.append(await detector.observe(user, question, space.point(cluster)))

    scores = [v.score for v in verdicts]
    assert max(scores) < ShieldSettings().semantic_threshold, scores
    assert verdicts[-1].features.novel_clusters == pytest.approx(2 / 30)


async def test_templated_enumerator_crosses_the_threshold_within_15_queries() -> None:
    space = Space()
    detector, clock = await _detector(space)
    user = uuid4()
    verdicts: list[SemanticVerdict] = []
    for i in range(CLUSTERS):
        clock.now += 10
        question = f"Opisz rozdział {i + 1} bazy wiedzy"
        verdicts.append(await detector.observe(user, question, space.point(i)))

    flagged_at = next(i + 1 for i, v in enumerate(verdicts) if v.anomalous)
    assert flagged_at <= 15
    f = verdicts[-1].features
    assert f.novel_clusters == 1.0
    assert f.template == 1.0  # digits folded: one template
    assert f.consecutive > 0.8
    assert f.regularity > 0.6


async def test_paraphrasing_enumerator_is_still_flagged() -> None:
    """Varied wording removes the template signal; the walk across clusters remains."""
    space = Space()
    detector, clock = await _detector(space)
    user = uuid4()
    anomalous: list[bool] = []
    for i in range(15):
        clock.now += 45
        question = BEEKEEPER_QUESTIONS[i % len(BEEKEEPER_QUESTIONS)]
        anomalous.append((await detector.observe(user, question, space.point(i))).anomalous)

    assert any(anomalous)


async def test_too_few_queries_score_zero() -> None:
    space = Space()
    detector, _ = await _detector(space)
    user = uuid4()
    verdicts = [
        await detector.observe(user, f"Opisz rozdział {i}", space.point(i)) for i in range(4)
    ]
    assert verdicts[-1].score == 0.0
    assert verdicts[-1].features.queries == 4


async def test_without_clusters_the_cluster_feature_is_off() -> None:
    space = Space()
    detector, _ = await _detector(None)
    user = uuid4()
    verdicts = [
        await detector.observe(user, f"Opisz rozdział {i}", space.point(i)) for i in range(6)
    ]
    assert verdicts[-1].features.novel_clusters == 0.0
    assert verdicts[-1].features.consecutive > 0.8


async def test_history_is_bounded_and_holds_no_query_text() -> None:
    redis = FakeAsyncRedis()
    settings = ShieldSettings(semantic_history=10)
    detector = SemanticAnomalyDetector(redis, settings)
    space, user = Space(), uuid4()
    for i in range(25):
        await detector.observe(user, "Jak leczyć warrozę zimą?", space.point(i % CLUSTERS))

    stored = await redis.lrange(f"sem:{user}", 0, -1)  # pyright: ignore[reportUnknownMemberType]
    assert len(stored) == 10  # pyright: ignore[reportUnknownArgumentType]
    dump = "".join(r.decode() if isinstance(r, bytes) else r for r in stored)
    assert "warroz" not in dump
    assert "leczy" not in dump
    assert 0 < await redis.ttl(f"sem:{user}") <= 7 * 24 * 3600


def test_tokens_fold_diacritics_digits_and_stopwords() -> None:
    assert tokens("Opisz rozdział 12 w bazie!") == {"opisz", "rozdzial", "#", "bazie"}
    assert jaccard(tokens("Opisz rozdział 7"), tokens("Opisz rozdział 8")) == 1.0
    assert jaccard(set(), set()) == 0.0


# --- Clusters -----------------------------------------------------------------------------------


def _clustered(space: Space, per_cluster: int = 15) -> tuple[NDArray[np.float32], list[int]]:
    labels = [c for c in range(CLUSTERS) for _ in range(per_cluster)]
    points = np.asarray([space.point(c) for c in labels], dtype=np.float32)
    return points, labels


def test_kmeans_recovers_separated_clusters() -> None:
    space = Space()
    points, truth = _clustered(space)

    found = nearest(kmeans(points, CLUSTERS, seed=0), points).tolist()

    # Purity: each found cluster is (almost) one true cluster.
    groups: dict[int, list[int]] = {}
    for f, t in zip(found, truth, strict=True):
        groups.setdefault(f, []).append(t)
    pure = sum(max(g.count(t) for t in set(g)) for g in groups.values())
    assert pure / len(truth) >= 0.95


def test_kmeans_does_not_depend_on_input_order() -> None:
    points, _ = _clustered(Space(), per_cluster=5)
    shuffled = np.random.default_rng(1).permutation(points)
    np.testing.assert_array_equal(kmeans(points, CLUSTERS), kmeans(shuffled, CLUSTERS))


def test_kmeans_caps_k_at_the_number_of_points() -> None:
    points = np.asarray(Space().rng.normal(size=(3, DIM)), dtype=np.float32)
    assert kmeans(points, 10).shape == (3, DIM)


def test_centroids_roundtrip() -> None:
    centroids = Space().centroids
    np.testing.assert_array_equal(decode(encode(centroids)), centroids)


class FakeVectors:
    def __init__(self, batches: list[list[list[float]]]) -> None:
        self.batches = batches

    async def active_vectors(self) -> AsyncIterator[list[list[float]]]:
        for batch in self.batches:
            yield batch


async def test_refresh_clusters_stores_centroids() -> None:
    space = Space()
    points, _ = _clustered(space, per_cluster=5)
    rows = [[float(x) for x in p] for p in points]
    redis = FakeAsyncRedis()

    k = await refresh_clusters(FakeVectors([rows[:50], rows[50:]]), redis, CLUSTERS)

    stored = await load_clusters(redis)
    assert k == CLUSTERS
    assert stored is not None
    assert stored.shape == (CLUSTERS, DIM)
    assert await refresh_clusters(FakeVectors([]), redis, CLUSTERS) == 0
    assert await load_clusters(redis) is not None  # an empty corpus keeps the old centroids
