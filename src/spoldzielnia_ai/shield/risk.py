"""Per-account risk (5.4, 5.6, SECURITY.md layer 5).

``RiskStore``: incidents (a honeypot hit, a blocked verbatim answer, an extraction-style
question …) add points to ``risk:{user}`` in Redis. The points decay exponentially
(half-life ``risk_half_life_days``): an account that stops misbehaving recovers on its own.
Kept in [0, 1]; a snapshot goes to ``app_user.risk_score`` for the admin panel (best effort:
Redis is authoritative).

``RiskEngine``: the score for one request = incidents + ``risk_coverage_weight`` * (how close
the account is to a coverage lock, ratio and absolute floor alike)^2 + ``risk_semantic_weight``
* semantic score, capped at 1. Levels and what the guard does with them:

=========  ===========  ======================================================
NORMAL     < 0.3        top_k 8
ELEVATED   0.3 to 0.5   top_k 4, shorter answers, extra jittered latency
HIGH       0.5 to 0.7   top_k 2 + step-up: fresh MFA login or proof-of-work
CRITICAL   >= 0.7       account locked (403 security_lock), admin alert
=========  ===========  ======================================================

A semantic score at ``semantic_hard_threshold`` is CRITICAL on its own.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import update

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.models import User

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.shield.coverage import CoverageStatus
    from spoldzielnia_ai.shield.semantic import SemanticVerdict

log = get_logger(__name__)

TTL_HALF_LIVES = 8  # after 8 half-lives (< 0.4% left) the key may expire


def decayed(value: float, since: float, now: float, half_life: float) -> float:
    return value * 0.5 ** (max(now - since, 0.0) / half_life)


class RiskStore:
    def __init__(
        self,
        redis: Redis,
        settings: ShieldSettings,
        factory: SessionFactory | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._half_life = settings.risk_half_life_days * 86400
        self._factory = factory
        self._clock = clock

    @staticmethod
    def _key(user_id: UUID) -> str:
        return f"risk:{user_id}"

    async def incidents(self, user_id: UUID) -> float:
        """The decayed incident score now."""
        raw = await self._redis.hgetall(self._key(user_id))
        fields = {
            (k.decode() if isinstance(k, bytes) else str(k)): float(v) for k, v in dict(raw).items()
        }
        if "v" not in fields:
            return 0.0
        return decayed(fields["v"], fields.get("t", 0.0), self._clock(), self._half_life)

    async def add(self, user_id: UUID, points: float) -> float:
        """Add ``points`` (after decaying what is there). Returns the new score."""
        now = self._clock()
        score = min(await self.incidents(user_id) + points, 1.0)
        key = self._key(user_id)
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(key, mapping={"v": score, "t": now})
        pipe.expire(key, int(self._half_life * TTL_HALF_LIVES))
        await pipe.execute()
        await self.snapshot(user_id, score)
        return score

    async def snapshot(self, user_id: UUID, score: float) -> None:
        if self._factory is None:
            return
        try:
            async with self._factory() as session, session.begin():
                await session.execute(
                    update(User).where(User.id == user_id).values(risk_score=round(score, 4))
                )
        except Exception as exc:
            log.warning("shield.risk_snapshot_failed", error=type(exc).__name__)


class RiskLevel(StrEnum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class Assessment:
    score: float
    level: RiskLevel
    incidents: float
    coverage: float  # the coverage term
    semantic: float  # the semantic term

    def to_json(self) -> dict[str, float | str]:
        return {
            "level": self.level.value,
            "score": round(self.score, 4),
            "incidents": round(self.incidents, 4),
            "coverage_term": round(self.coverage, 4),
            "semantic_term": round(self.semantic, 4),
        }


class RiskEngine:
    def __init__(self, store: RiskStore, settings: ShieldSettings) -> None:
        self._store = store
        self._settings = settings

    def level(self, score: float) -> RiskLevel:
        s = self._settings
        if score >= s.risk_critical:
            return RiskLevel.CRITICAL
        if score >= s.risk_high:
            return RiskLevel.HIGH
        if score >= s.risk_elevated:
            return RiskLevel.ELEVATED
        return RiskLevel.NORMAL

    async def assess(
        self, user_id: UUID, coverage: CoverageStatus, semantic: SemanticVerdict
    ) -> Assessment:
        s = self._settings
        incidents = await self._store.incidents(user_id)
        coverage_term = s.risk_coverage_weight * coverage.pressure(s.min_blocks_before_lock) ** 2
        semantic_term = s.risk_semantic_weight * semantic.score
        score = min(incidents + coverage_term + semantic_term, 1.0)
        level = RiskLevel.CRITICAL if semantic.hard else self.level(score)
        return Assessment(score, level, incidents, coverage_term, semantic_term)
