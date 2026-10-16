from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import String

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.verify import GENESIS_HASH, ChainBreak, ChainRow, check_chain, row_hash
from spoldzielnia_ai.db.models import SecurityAuditLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable


def test_event_types_fit_the_column() -> None:
    column_type = SecurityAuditLog.__table__.c["event_type"].type
    assert isinstance(column_type, String)
    assert column_type.length is not None
    for event in EventType:
        assert event.value == event.name
        assert len(event.value) <= column_type.length


def _chain(n: int) -> list[ChainRow]:
    rows: list[ChainRow] = []
    prev = GENESIS_HASH
    for i in range(1, n + 1):
        canonical = f'{{"id": {i}, "payload": {{}}}}'
        rows.append(ChainRow(i, prev, row_hash(prev, canonical), canonical))
        prev = rows[-1].hash
    return rows


async def _aiter(rows: Iterable[ChainRow]) -> AsyncIterator[ChainRow]:
    for row in rows:
        yield row


def test_row_hash_is_sha256_of_prev_and_utf8_text() -> None:
    import hashlib

    assert row_hash(GENESIS_HASH, "ż") == hashlib.sha256(bytes(32) + "ż".encode()).digest()


async def test_intact_chain() -> None:
    rows = _chain(4)
    report = await check_chain(_aiter(rows))
    assert report.ok
    assert report.rows == 4
    assert report.head_id == 4
    assert report.head_hash == rows[-1].hash


async def test_empty_chain() -> None:
    report = await check_chain(_aiter([]))
    assert report.ok
    assert report.head_id is None
    assert report.head_hash == GENESIS_HASH


async def test_edited_row_reported_once() -> None:
    rows = _chain(4)
    edited = rows[1]
    rows[1] = ChainRow(edited.id, edited.prev_hash, edited.hash, '{"id": 2, "payload": {"x": 1}}')
    report = await check_chain(_aiter(rows))
    assert report.breaks == [ChainBreak(2, "content")]


@pytest.mark.parametrize("missing", [0, 1, 2])
async def test_removed_row_breaks_next_link(missing: int) -> None:
    rows = _chain(4)
    del rows[missing]
    report = await check_chain(_aiter(rows))
    assert report.breaks == [ChainBreak(missing + 2, "prev_link")]


async def test_swapped_rows_detected() -> None:
    rows = _chain(4)
    rows[1], rows[2] = rows[2], rows[1]
    report = await check_chain(_aiter(rows))
    assert not report.ok
    assert {brk.reason for brk in report.breaks} == {"prev_link"}
