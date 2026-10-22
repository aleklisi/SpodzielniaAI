"""``SemanticAnomalyDetector``: how a session moves through embedding space (5.3).

A member asks about their own problem: queries stay near one or two topics, distances between
them vary, wording varies. A scraper sweeps the corpus breadth-first or walks it cluster by
cluster ("Opisz rozdział 1/2/3…"): large, uniform steps, a new topic almost every time,
templated wording, at machine pace (SECURITY.md layer 2).

History: the last ``semantic_history`` queries per account in Redis (``sem:{user}``, TTL 7d),
each as its time, its float32 embedding and **hashes** of its normalised tokens (no query
text is stored).

Features (the current query included):
- ``consecutive``: mean cosine distance between consecutive queries (breadth-first sweep);
- ``dispersion``: mean cosine distance to the session's centroid;
- ``novel_clusters``: distinct canonical clusters visited / queries (grid-walking). Clusters
  come from the nightly k-means (``shield.clusters``); without them the feature is 0;
- ``regularity``: 1 - coefficient of variation of the consecutive distances (bot-like steps);
- ``template``: mean Jaccard similarity of the query's tokens to the previous 10 (digits
  folded, so "rozdział 7" and "rozdział 8" are one template);
- ``rate``: queries in the last hour / ``semantic_rate_ref_per_hour`` (capped at 1).

Score = sigmoid(bias + Σ wᵢ·featureᵢ), 0 until ``semantic_min_queries`` queries are known. A
rule-based starting point: the weights live in settings and are meant to be calibrated on real
query logs. The score feeds the RiskEngine (5.6); it blocks on its own only at
``semantic_hard_threshold``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import numpy as np

from spoldzielnia_ai.shield.clusters import load_clusters, nearest, normalize

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from pydantic import JsonValue
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import ShieldSettings

HISTORY_TTL_SECONDS = 7 * 24 * 3600
TEMPLATE_WINDOW = 10
_WORD = re.compile(r"\w+")
_DIGITS = re.compile(r"\d+")
STOPWORDS = frozenset(
    (  # noqa: SIM905 - one readable line of words
        "a aby albo ale co czy do dla i jak jaki jakie jest lub na nie o od oraz po przez się "
        "to w we z za ze że the of and or in is what how"
    ).split()
)


def tokens(question: str) -> set[str]:
    """Lower-case, diacritics folded, digits → ``#``, stopwords and 1-letter words dropped."""
    folded = unicodedata.normalize("NFKD", question.lower().replace("ł", "l"))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    words = (_DIGITS.sub("#", w) for w in _WORD.findall(folded))
    return {w for w in words if (len(w) > 1 or w == "#") and w not in STOPWORDS}


def token_hashes(question: str) -> list[str]:
    return sorted(hashlib.blake2b(t.encode(), digest_size=4).hexdigest() for t in tokens(question))


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


@dataclass(frozen=True, slots=True)
class Features:
    queries: int
    consecutive: float = 0.0
    dispersion: float = 0.0
    novel_clusters: float = 0.0
    regularity: float = 0.0
    template: float = 0.0
    rate: float = 0.0

    def to_json(self) -> dict[str, JsonValue]:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class SemanticVerdict:
    score: float
    features: Features
    anomalous: bool  # score >= semantic_threshold
    hard: bool  # score >= semantic_hard_threshold: block regardless of other signals


@dataclass(frozen=True, slots=True)
class _Entry:
    at: float
    vector: np.ndarray
    hashes: set[str]

    def dump(self) -> str:
        data = base64.b64encode(self.vector.astype(np.float32).tobytes()).decode()
        return json.dumps({"t": self.at, "v": data, "k": sorted(self.hashes)})

    @classmethod
    def load(cls, raw: str | bytes) -> _Entry:
        doc = json.loads(raw)
        vector = np.frombuffer(base64.b64decode(doc["v"]), dtype=np.float32)
        return cls(float(doc["t"]), vector, set(doc["k"]))


def _cosine_distances(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cosine distance between unit-normalised rows of ``a`` and ``b``."""
    return 1.0 - np.sum(a * b, axis=1)


class SemanticAnomalyDetector:
    def __init__(
        self,
        redis: Redis,
        settings: ShieldSettings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._clock = clock

    @staticmethod
    def _key(user_id: UUID) -> str:
        return f"sem:{user_id}"

    async def _history(self, user_id: UUID) -> list[_Entry]:
        raw = await self._redis.lrange(self._key(user_id), 0, self._settings.semantic_history - 1)
        return [_Entry.load(r) for r in reversed(list(raw))]

    async def observe(self, user_id: UUID, question: str, vector: list[float]) -> SemanticVerdict:
        """Score the session with this query added, and remember the query."""
        array = np.asarray(vector, dtype=np.float32)
        entry = _Entry(self._clock(), array, set(token_hashes(question)))
        history = [*await self._history(user_id), entry][-self._settings.semantic_history :]
        key = self._key(user_id)
        pipe = self._redis.pipeline(transaction=True)
        pipe.lpush(key, entry.dump())
        pipe.ltrim(key, 0, self._settings.semantic_history - 1)
        pipe.expire(key, HISTORY_TTL_SECONDS)
        await pipe.execute()
        return await self.score(history)

    async def score(self, history: list[_Entry]) -> SemanticVerdict:
        s = self._settings
        features = await self.features(history)
        if features.queries < s.semantic_min_queries:
            return SemanticVerdict(0.0, features, anomalous=False, hard=False)
        z = (
            s.semantic_bias
            + s.semantic_w_consecutive * features.consecutive
            + s.semantic_w_dispersion * features.dispersion
            + s.semantic_w_novel_clusters * features.novel_clusters
            + s.semantic_w_regularity * features.regularity
            + s.semantic_w_template * features.template
            + s.semantic_w_rate * features.rate
        )
        value = 1.0 / (1.0 + math.exp(-z))
        return SemanticVerdict(
            value,
            features,
            anomalous=value >= s.semantic_threshold,
            hard=value >= s.semantic_hard_threshold,
        )

    async def features(self, history: list[_Entry]) -> Features:
        if history:  # after an embedding-model change (10.3), older entries are another space
            size = history[-1].vector.shape[0]
            history = [e for e in history if e.vector.shape[0] == size]
        n = len(history)
        if n < 2:
            return Features(queries=n)
        unit = normalize(np.stack([e.vector for e in history]))
        steps = _cosine_distances(unit[1:], unit[:-1])
        centroid = normalize(unit.mean(axis=0, keepdims=True))
        dispersion = float(np.mean(_cosine_distances(unit, np.repeat(centroid, n, axis=0))))
        mean_step = float(np.mean(steps))
        regularity = 0.0
        if len(steps) >= 2 and mean_step > 0:
            regularity = float(np.clip(1.0 - np.std(steps) / mean_step, 0.0, 1.0))
        novel = 0.0
        clusters = await load_clusters(self._redis)
        if clusters is not None and clusters.shape[1] == unit.shape[1]:
            novel = len(set(nearest(clusters, unit).tolist())) / n
        current = history[-1].hashes
        previous = history[-1 - TEMPLATE_WINDOW : -1]
        template = float(np.mean([jaccard(current, e.hashes) for e in previous]))
        now = history[-1].at
        recent = sum(1 for e in history if now - e.at <= 3600)
        rate = min(recent / self._settings.semantic_rate_ref_per_hour, 1.0)
        return Features(n, mean_step, dispersion, novel, regularity, template, rate)
