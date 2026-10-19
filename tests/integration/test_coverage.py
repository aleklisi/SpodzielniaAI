"""Chunk 5.2: coverage against real Redis (HLL error), the Postgres denominator, and the Wiki.

PLAN Etap 5: blocking at the 15% boundary must hold within HyperLogLog's error (±2%).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import SecurityAuditLog, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.shield.coverage import (
    CorpusCounter,
    CoverageExceeded,
    CoverageLevel,
    CoverageTracker,
    Window,
)
from tests.fixtures.app import harness
from tests.fixtures.shield import FakeLocker, FixedCorpus
from tests.fixtures.wiki import make_article, make_user

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(redis_url)  # pyright: ignore[reportUnknownMemberType]
    await client.flushdb()  # pyright: ignore[reportUnknownMemberType]
    yield client
    await client.aclose()


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.mark.parametrize(("touched", "locked"), [(1400, False), (1600, True)])
async def test_hll_decides_correctly_near_the_15_percent_boundary(
    redis: Redis, touched: int, locked: bool
) -> None:
    """10 000 blocks: 14% and 16% are 1 percentage point (~7%) away from the limit, far
    outside HLL's error, and the count itself stays within ±2%."""
    tracker = CoverageTracker(
        redis,
        FixedCorpus(10_000),
        ShieldSettings(coverage_24h_max=0.15, min_blocks_before_lock=0),
        FakeLocker(),
    )
    user = uuid4()
    blocks = [uuid4() for _ in range(touched)]
    for start in range(0, touched, 100):  # requests of top_k-ish batches
        await tracker.record(user, blocks[start : start + 100], [])

    status = await tracker.check(user)

    counted = status.usage[Window.D7].blocks
    assert abs(counted - touched) <= 0.02 * touched, counted
    assert (status.level == CoverageLevel.LOCK) is locked


async def test_corpus_size_counts_retrievable_blocks_only(
    factory: SessionFactory, redis: Redis
) -> None:
    author = await make_user(factory)
    await make_article(factory, [(author, "a"), (author, "b"), (author, "c")])
    await make_article(
        factory,
        [(author, "d"), (author, "e")],
        consents=[ConsentStatus.ACTIVE, ConsentStatus.SUSPENDED],
    )
    await make_article(factory, [(author, "f")], status=ArticleStatus.DRAFT)
    corpus = CorpusCounter(redis, factory, ttl_seconds=60)

    size = await corpus.size()

    assert (size.blocks, size.articles) == (4, 2)
    await make_article(factory, [(author, "g")])
    assert (await corpus.size()).blocks == 4  # cached
    await corpus.invalidate()
    assert (await corpus.size()).blocks == 5


async def test_reading_the_wiki_counts_and_locks(
    settings: Settings, factory: SessionFactory
) -> None:
    author = await make_user(factory)
    articles = [
        (await make_article(factory, [(author, f"Treść artykułu {i}")], title=f"A{i}"))[0]
        for i in range(10)
    ]
    # The database is shared between tests: size the limit to the corpus there is. One block
    # read stays under it, two cross it (7d, the more serious window, decides).
    total = (await CorpusCounter(FakeAsyncRedis(), factory, 60).size()).blocks
    limit = 1.5 / total
    shield = ShieldSettings(
        coverage_24h_max=limit,
        coverage_7d_max=limit,
        coverage_30d_max=0.99,
        min_blocks_before_lock=2,
    )
    async with harness(settings.model_copy(update={"shield": shield}), factory) as h:
        reader = await h.login()
        first = await reader.client.get(f"/wiki/articles/{articles[0]}")
        second = await reader.client.get(f"/wiki/articles/{articles[1]}")
        after = await reader.client.get("/wiki/articles")

    assert first.status_code == 200
    assert second.status_code == 403
    assert second.json()["detail"] == {"error": "security_lock"}
    assert after.status_code == 423  # the account is locked, its sessions dropped
    async with factory() as session:
        user = await session.get(User, UUID(reader.user_id))
        assert user is not None
        assert (user.is_locked, user.locked_reason) == (True, "coverage_7d")
        assert user.locked_at is not None
        entry = await session.scalar(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.COVERAGE_LOCK.value
            )
        )
    assert entry is not None
    assert entry.actor_id == UUID(reader.user_id)
    assert entry.payload["window"] == "7d"
    assert entry.payload["blocks"] == 2


async def test_authors_reading_their_own_article_are_not_counted(
    settings: Settings, factory: SessionFactory
) -> None:
    shield = ShieldSettings(min_blocks_before_lock=1, coverage_24h_max=0.01)
    async with harness(settings.model_copy(update={"shield": shield}), factory) as h:
        author = await h.login()
        article, _ = await make_article(factory, [(UUID(author.user_id), "Moja wiedza")])
        responses = [await author.client.get(f"/wiki/articles/{article}") for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 200]


async def test_admit_raises_before_recording(redis: Redis) -> None:
    tracker = CoverageTracker(
        redis, FixedCorpus(10), ShieldSettings(min_blocks_before_lock=0), FakeLocker()
    )
    user = uuid4()
    with pytest.raises(CoverageExceeded):
        await tracker.admit(user, [uuid4(), uuid4()], [])
    assert (await tracker.check(user)).usage[Window.H24].blocks == 0
