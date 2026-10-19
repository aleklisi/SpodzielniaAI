"""``CoverageTracker``: how much of the canonical corpus one account has touched (5.2).

Rate limits count requests. A slow bot staying under them still walks the whole corpus, so
this counts **unique blocks and articles** per account in rolling windows (SECURITY.md layer 1).

Keys (Redis HyperLogLog, ~0.8% standard error, 12 KB per key at most):
- ``cov:{user}:h:{yyyymmddhh}:{b|a}``: hourly buckets for the 24h window (the last 24), TTL 25h;
- ``cov:{user}:d:{yyyymmdd}:{b|a}``: daily buckets for the 7d and 30d windows, TTL 31 days.
A window's count is ``PFCOUNT`` over its buckets (the union, computed by Redis).

Denominator: retrievable canonical blocks (ACTIVE, with content, CANONICAL article, never
honeypots), counted in Postgres and cached in Redis for ``corpus_size_ttl_seconds``.

What counts: blocks admitted to synthesis by the guard (5.6) and blocks rendered in canonical
Wiki views. **Ordering:** ``admit`` evaluates the windows *including* the candidate blocks and
records them only if allowed, so the request that would cross a limit is the one refused.
Two concurrent requests of one account can both pass the check: the overshoot is bounded by
one request's ``top_k`` and the next request is refused.

Levels: ``LOCK`` when a window's ratio exceeds its limit and the window holds at least
``min_blocks_before_lock`` blocks (gap G14), ``WARN`` from ``coverage_warn_fraction`` of a
limit. A 24h LOCK is a throttle (429 + ``Retry-After``); a 7d/30d LOCK locks the account (403
``security_lock``), audits ``COVERAGE_LOCK`` and alerts an admin.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func, select

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.shield.locks import Locker

log = get_logger(__name__)

HOUR_TTL = timedelta(hours=25)
DAY_TTL = timedelta(days=31)
CORPUS_KEY = "cov:corpus"
THROTTLE_AUDIT_EVERY = timedelta(hours=1)


class Window(StrEnum):
    H24 = "24h"
    D7 = "7d"
    D30 = "30d"


# Most serious first: the first window that trips decides the response.
SEVERITY = (Window.D30, Window.D7, Window.H24)


class CoverageLevel(StrEnum):
    OK = "OK"
    WARN = "WARN"
    LOCK = "LOCK"


@dataclass(frozen=True, slots=True)
class CorpusSize:
    blocks: int
    articles: int


@dataclass(frozen=True, slots=True)
class WindowUsage:
    blocks: int
    articles: int
    ratio: float  # unique blocks / retrievable blocks
    limit: float


@dataclass(frozen=True, slots=True)
class CoverageStatus:
    usage: dict[Window, WindowUsage]
    level: CoverageLevel
    tripped: Window | None = None

    @property
    def ratio_by_window(self) -> dict[Window, float]:
        return {w: u.ratio for w, u in self.usage.items()}

    @property
    def max_fraction_of_limit(self) -> float:
        """How close the closest window is to its limit (1.0 = at the limit). Risk input."""
        return max((u.ratio / u.limit for u in self.usage.values()), default=0.0)


class CoverageExceeded(Exception):
    def __init__(self, status: CoverageStatus, retry_after: int | None) -> None:
        super().__init__(f"coverage {status.tripped}")
        self.status = status
        self.retry_after = retry_after  # 24h throttle only

    @property
    def account_locked(self) -> bool:
        return self.status.tripped in (Window.D7, Window.D30)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Corpus(Protocol):
    async def size(self) -> CorpusSize: ...


class CorpusCounter:
    """The denominator, cached briefly in Redis (one Postgres count per TTL, not per query)."""

    def __init__(self, redis: Redis, factory: SessionFactory, ttl_seconds: int) -> None:
        self._redis = redis
        self._factory = factory
        self._ttl = ttl_seconds

    async def size(self) -> CorpusSize:
        cached = await self._redis.get(CORPUS_KEY)
        if cached is not None:
            raw = cached.decode() if isinstance(cached, bytes) else cached
            blocks, articles = (int(x) for x in raw.split(":"))
            return CorpusSize(blocks, articles)
        counts = (
            select(func.count(ArticleBlock.id), func.count(ArticleBlock.article_id.distinct()))
            .join(WikiArticle, WikiArticle.id == ArticleBlock.article_id)
            .where(
                ArticleBlock.consent_status == ConsentStatus.ACTIVE,
                ArticleBlock.content.is_not(None),
                ArticleBlock.is_honeypot.is_(False),
                WikiArticle.status == ArticleStatus.CANONICAL,
            )
        )
        async with self._factory() as session:
            blocks, articles = (await session.execute(counts)).one()
        size = CorpusSize(int(blocks), int(articles))
        await self._redis.set(CORPUS_KEY, f"{size.blocks}:{size.articles}", ex=self._ttl)
        return size

    async def invalidate(self) -> None:
        await self._redis.delete(CORPUS_KEY)


def _hour_buckets(user_id: UUID, now: datetime) -> list[str]:
    return [f"cov:{user_id}:h:{now - timedelta(hours=i):%Y%m%d%H}" for i in range(24)]


def _day_buckets(user_id: UUID, now: datetime, days: int) -> list[str]:
    return [f"cov:{user_id}:d:{now - timedelta(days=i):%Y%m%d}" for i in range(days)]


class CoverageTracker:
    def __init__(
        self,
        redis: Redis,
        corpus: Corpus,
        settings: ShieldSettings,
        locker: Locker | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._redis = redis
        self._corpus = corpus
        self._settings = settings
        self._locker = locker
        self._clock = clock

    def _limits(self) -> dict[Window, float]:
        s = self._settings
        return {
            Window.H24: s.coverage_24h_max,
            Window.D7: s.coverage_7d_max,
            Window.D30: s.coverage_30d_max,
        }

    def _buckets(self, user_id: UUID, window: Window, now: datetime) -> list[str]:
        match window:
            case Window.H24:
                return _hour_buckets(user_id, now)
            case Window.D7:
                return _day_buckets(user_id, now, 7)
            case Window.D30:
                return _day_buckets(user_id, now, 30)

    async def _count(self, keys: list[str], extra: Sequence[UUID]) -> int:
        if not extra:
            return int(await self._redis.pfcount(*keys))
        temp = f"cov:tmp:{secrets.token_hex(8)}"
        pipe = self._redis.pipeline(transaction=False)
        pipe.pfadd(temp, *(str(i) for i in extra))
        pipe.pfcount(*keys, temp)
        pipe.delete(temp)
        _, count, _ = await pipe.execute()
        return int(count)

    async def status(
        self,
        user_id: UUID,
        block_ids: Sequence[UUID] = (),
        article_ids: Sequence[UUID] = (),
    ) -> CoverageStatus:
        """Coverage as it would be after touching ``block_ids`` (nothing is recorded)."""
        now = self._clock()
        corpus = await self._corpus.size()
        usage: dict[Window, WindowUsage] = {}
        for window, limit in self._limits().items():
            buckets = self._buckets(user_id, window, now)
            blocks = await self._count([f"{b}:b" for b in buckets], block_ids)
            articles = await self._count([f"{b}:a" for b in buckets], article_ids)
            ratio = blocks / corpus.blocks if corpus.blocks else float(blocks > 0)
            usage[window] = WindowUsage(blocks, articles, ratio, limit)
        return CoverageStatus(usage, *self._level(usage))

    def _level(self, usage: dict[Window, WindowUsage]) -> tuple[CoverageLevel, Window | None]:
        floor = self._settings.min_blocks_before_lock
        for window in SEVERITY:
            u = usage[window]
            if u.ratio > u.limit and u.blocks >= floor:
                return CoverageLevel.LOCK, window
        warn = self._settings.coverage_warn_fraction
        if any(u.ratio >= warn * u.limit for u in usage.values()):
            return CoverageLevel.WARN, None
        return CoverageLevel.OK, None

    async def check(self, user_id: UUID) -> CoverageStatus:
        return await self.status(user_id)

    async def record(
        self, user_id: UUID, block_ids: Sequence[UUID], article_ids: Sequence[UUID]
    ) -> None:
        if not block_ids and not article_ids:
            return
        now = self._clock()
        hour = f"cov:{user_id}:h:{now:%Y%m%d%H}"
        day = f"cov:{user_id}:d:{now:%Y%m%d}"
        pipe = self._redis.pipeline(transaction=False)
        for bucket, ttl in ((hour, HOUR_TTL), (day, DAY_TTL)):
            if block_ids:
                pipe.pfadd(f"{bucket}:b", *(str(b) for b in block_ids))
                pipe.expire(f"{bucket}:b", ttl)
            if article_ids:
                pipe.pfadd(f"{bucket}:a", *(str(a) for a in article_ids))
                pipe.expire(f"{bucket}:a", ttl)
        await pipe.execute()

    async def admit(
        self, user_id: UUID, block_ids: Sequence[UUID], article_ids: Sequence[UUID]
    ) -> CoverageStatus:
        """Record ``block_ids`` unless that would cross a limit: then raise
        ``CoverageExceeded`` (after locking the account for a 7d/30d window)."""
        status = await self.status(user_id, block_ids, article_ids)
        if status.level != CoverageLevel.LOCK:
            await self.record(user_id, block_ids, article_ids)
            return status
        await self._on_lock(user_id, status)
        retry_after = None
        if status.tripped == Window.H24:
            now = self._clock()
            retry_after = 3600 - (now.minute * 60 + now.second)
        raise CoverageExceeded(status, retry_after)

    async def _on_lock(self, user_id: UUID, status: CoverageStatus) -> None:
        window = status.tripped
        assert window is not None  # noqa: S101 - LOCK always names its window
        u = status.usage[window]
        detail: dict[str, float | int | str] = {
            "window": window.value,
            "blocks": u.blocks,
            "articles": u.articles,
            "ratio": round(u.ratio, 4),
            "limit": u.limit,
        }
        log.warning("shield.coverage_lock", user_id=str(user_id), **detail)
        if self._locker is None:
            return
        if window != Window.H24:
            await self._locker.lock(
                user_id, f"coverage_{window.value}", EventType.COVERAGE_LOCK, dict(detail)
            )
            return
        # The 24h throttle is audited at most hourly per account, not on every refused request.
        flag = f"cov:{user_id}:throttle_audited"
        if await self._redis.set(flag, "1", ex=THROTTLE_AUDIT_EVERY, nx=True):
            await self._locker.audit(user_id, EventType.COVERAGE_LOCK, {**detail, "throttle": True})
