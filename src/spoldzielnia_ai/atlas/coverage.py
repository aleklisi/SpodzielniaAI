"""The atlas as a corpus for the shield's coverage budget (9.5, A7).

Viewing an atlas photo (its page or its full image) counts against the viewer's budget like
reading wiki text does, under its own Redis prefix: a scraper walking the atlas trips the same
24h/7d/30d limits (and the same absolute floor before any lock) as one walking the wiki.
Authors viewing their own photos and experts reviewing don't count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus
from spoldzielnia_ai.db.models import AtlasImage
from spoldzielnia_ai.shield.coverage import CorpusSize, CoverageTracker

if TYPE_CHECKING:
    from fastapi import Request
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory

PREFIX = "covatlas"
CORPUS_KEY = f"{PREFIX}:corpus"


class AtlasCorpus:
    """Approved photos with ACTIVE consent (``articles`` = their domains)."""

    def __init__(self, redis: Redis, factory: SessionFactory, ttl_seconds: int) -> None:
        self._redis = redis
        self._factory = factory
        self._ttl = ttl_seconds

    async def size(self) -> CorpusSize:
        cached = await self._redis.get(CORPUS_KEY)
        if cached is not None:
            raw = cached.decode() if isinstance(cached, bytes) else cached
            images, domains = (int(x) for x in raw.split(":"))
            return CorpusSize(images, domains)
        counts = select(
            func.count(AtlasImage.id), func.count(AtlasImage.domain_id.distinct())
        ).where(
            AtlasImage.status == AtlasImageStatus.APPROVED,
            AtlasImage.consent_status == ConsentStatus.ACTIVE,
        )
        async with self._factory() as session:
            images, domains = (await session.execute(counts)).one()
        size = CorpusSize(int(images), int(domains))
        await self._redis.set(CORPUS_KEY, f"{size.blocks}:{size.articles}", ex=self._ttl)
        return size


async def invalidate_corpus(redis: Redis) -> None:
    await redis.delete(CORPUS_KEY)


def atlas_coverage(request: Request) -> CoverageTracker:
    from spoldzielnia_ai.shield.deps import get_locker

    state = request.app.state
    settings: Settings = state.settings
    corpus = AtlasCorpus(
        state.redis, state.session_factory, settings.shield.corpus_size_ttl_seconds
    )
    return CoverageTracker(state.redis, corpus, settings.shield, get_locker(request), prefix=PREFIX)
