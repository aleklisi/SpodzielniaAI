"""Chunk 5.2: ``CoverageTracker`` windows, ordering, levels and the small-corpus floor.

fakeredis counts HyperLogLogs exactly; the HLL error at the boundary is measured against real
Redis in ``tests/integration/test_coverage_hll.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fakeredis import FakeAsyncRedis
from freezegun import freeze_time

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.coverage import (
    CoverageExceeded,
    CoverageLevel,
    CoverageTracker,
    Window,
)
from spoldzielnia_ai.shield.deps import coverage_refusal
from tests.fixtures.shield import FakeLocker, FixedCorpus

START = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


def _ids(n: int) -> list[UUID]:
    return [uuid4() for _ in range(n)]


def _tracker(
    corpus: int = 1000, locker: FakeLocker | None = None, **settings: float
) -> CoverageTracker:
    config = ShieldSettings.model_validate({"min_blocks_before_lock": 0, **settings})
    return CoverageTracker(FakeAsyncRedis(), FixedCorpus(corpus, 100), config, locker)


async def test_records_unique_blocks_per_window() -> None:
    tracker, user = _tracker(), uuid4()
    blocks = _ids(10)
    await tracker.record(user, blocks, _ids(2))
    await tracker.record(user, blocks[:5], [])  # repeats don't count twice

    status = await tracker.check(user)

    assert status.ratio_by_window == {Window.H24: 0.01, Window.D7: 0.01, Window.D30: 0.01}
    assert status.usage[Window.D7].articles == 2
    assert status.level == CoverageLevel.OK
    assert (await tracker.check(uuid4())).usage[Window.D7].blocks == 0  # per account


async def test_windows_roll_over() -> None:
    tracker, user = _tracker(), uuid4()
    with freeze_time(START) as clock:
        await tracker.record(user, _ids(30), [])
        clock.tick(timedelta(hours=23))
        assert (await tracker.check(user)).usage[Window.H24].blocks == 30
        clock.tick(timedelta(hours=2))  # 25h later: out of the 24h window
        usage = (await tracker.check(user)).usage
        assert (usage[Window.H24].blocks, usage[Window.D7].blocks) == (0, 30)
        clock.tick(timedelta(days=7))
        usage = (await tracker.check(user)).usage
        assert (usage[Window.D7].blocks, usage[Window.D30].blocks) == (0, 30)
        clock.tick(timedelta(days=23))
        assert (await tracker.check(user)).usage[Window.D30].blocks == 0


async def test_admit_refuses_the_request_that_would_cross_the_7d_limit() -> None:
    locker = FakeLocker()
    tracker, user = _tracker(corpus=100, locker=locker, coverage_24h_max=0.15), uuid4()
    with freeze_time(START) as clock:
        await tracker.admit(user, _ids(10), [])  # 10%
        clock.tick(timedelta(days=1))
        await tracker.admit(user, _ids(5), [])  # 15%: at the limit, not over it
        clock.tick(timedelta(days=1))
        with pytest.raises(CoverageExceeded) as refused:
            await tracker.admit(user, _ids(1), [])  # would be 16%
        # The refused block was not recorded.
        assert (await tracker.check(user)).usage[Window.D7].blocks == 15

    assert refused.value.status.tripped == Window.D7
    assert refused.value.account_locked
    assert coverage_refusal(refused.value).status_code == 403
    assert locker.locked == [(user, "coverage_7d", EventType.COVERAGE_LOCK)]


async def test_24h_limit_throttles_without_locking_the_account() -> None:
    locker = FakeLocker()
    tracker, user = _tracker(corpus=100, locker=locker), uuid4()
    with freeze_time(START):
        await tracker.admit(user, _ids(5), [])
        with pytest.raises(CoverageExceeded) as refused:
            await tracker.admit(user, _ids(1), [])
        with pytest.raises(CoverageExceeded):
            await tracker.admit(user, _ids(1), [])

    exc = refused.value
    assert exc.status.tripped == Window.H24
    assert not exc.account_locked
    assert exc.retry_after == 30 * 60  # until the hour rolls
    response = coverage_refusal(exc)
    assert response.status_code == 429
    assert response.headers == {"Retry-After": "1800"}
    assert locker.locked == []
    assert len(locker.audited) == 1  # audited once per hour, not per refused request


async def test_small_corpus_floor() -> None:
    """Gap G14: 15% of 20 blocks is 3 blocks, so a ratio alone would lock almost anyone."""
    tracker, user = _tracker(corpus=20, min_blocks_before_lock=50), uuid4()

    status = await tracker.admit(user, _ids(20), [])

    assert status.ratio_by_window[Window.D7] == 1.0
    assert status.level == CoverageLevel.WARN


async def test_warn_before_lock() -> None:
    tracker, user = _tracker(corpus=1000), uuid4()
    await tracker.record(user, _ids(31), [])  # 3.1% >= 0.6 * 5%

    status = await tracker.check(user)

    assert status.level == CoverageLevel.WARN
    assert status.max_fraction_of_limit == pytest.approx(0.031 / 0.05)


async def test_status_with_candidates_records_nothing() -> None:
    tracker, user = _tracker(), uuid4()
    assert (await tracker.status(user, _ids(40), [])).usage[Window.D7].blocks == 40
    assert (await tracker.check(user)).usage[Window.D7].blocks == 0


def test_limits_must_widen_with_the_window() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ShieldSettings(coverage_24h_max=0.2, coverage_7d_max=0.15)
