from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.core import ids
from spoldzielnia_ai.core.ids import uuid7

if TYPE_CHECKING:
    import pytest


def test_uuid7_version_and_variant() -> None:
    value = uuid7()
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_embeds_unix_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_000_123_456_789)
    assert uuid7().int >> 80 == 1_700_000_000_123


def test_uuid7_sorts_by_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_700_000_000_000_000_000]
    monkeypatch.setattr(ids.time, "time_ns", lambda: now[0])
    earlier = uuid7()
    now[0] += 1_000_000  # +1 ms
    assert uuid7() > earlier


def test_uuid7_is_random_within_a_millisecond(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    assert len({uuid7() for _ in range(1000)}) == 1000
