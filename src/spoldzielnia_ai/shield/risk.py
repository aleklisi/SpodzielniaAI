"""Per-account risk from shield incidents (5.4, 5.6).

Incidents (a honeypot hit, a blocked verbatim answer, an extraction-style question …) add
points to ``risk:{user}`` in Redis. The points decay exponentially (half-life
``risk_half_life_days``): an account that stops misbehaving recovers on its own. The score is
kept in [0, 1] and a snapshot goes to ``app_user.risk_score`` for the admin panel (best
effort: Redis is authoritative).
"""

from __future__ import annotations

import time
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
