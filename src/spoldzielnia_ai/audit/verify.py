"""Hash-chain verifier behind ``spoldzielnia audit-verify``.

Postgres renders each row's canonical text with built-in functions only. The same expression is
in migration 0003. Linking and SHA-256 are checked here in Python, independent of the trigger
code. The chain detects edited, inserted or deleted rows anywhere *before* the head. Cutting off
the newest rows is detectable only against a head recorded elsewhere, so the report includes the
head id and hash for anchoring.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

    from sqlalchemy.ext.asyncio import AsyncEngine

GENESIS_HASH = bytes(32)

# Must match the expression in the BEFORE INSERT trigger (migration 0003).
CANONICAL_ROW_SQL = """
jsonb_build_object(
  'id', id,
  'occurred_at', to_char(occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'actor_id', actor_id,
  'event_type', event_type,
  'target_type', target_type,
  'target_id', target_id,
  'payload', payload
)::text
"""

_CHAIN_ROWS = text(
    f"SELECT id, prev_hash, hash, {CANONICAL_ROW_SQL} AS canonical "  # noqa: S608 - constant SQL
    "FROM security_audit_log ORDER BY id"
)


@dataclass(frozen=True, slots=True)
class ChainRow:
    id: int
    prev_hash: bytes
    hash: bytes
    canonical: str


@dataclass(frozen=True, slots=True)
class ChainBreak:
    id: int
    # prev_link: `prev_hash` is not the previous row's hash (row deleted, inserted or reordered).
    # content: `hash` does not match the row's content (row edited).
    reason: Literal["prev_link", "content"]


@dataclass(slots=True)
class ChainReport:
    rows: int = 0
    head_id: int | None = None
    head_hash: bytes = GENESIS_HASH
    breaks: list[ChainBreak] = field(default_factory=list[ChainBreak])

    @property
    def ok(self) -> bool:
        return not self.breaks


def row_hash(prev_hash: bytes, canonical: str) -> bytes:
    return hashlib.sha256(prev_hash + canonical.encode()).digest()


async def check_chain(rows: AsyncIterable[ChainRow]) -> ChainReport:
    """Walk ``rows`` in id order and report every break.

    After a break the walk continues from the stored hash, so one defect is reported once, not
    again for every row after it.
    """
    report = ChainReport()
    async for row in rows:
        if row.prev_hash != report.head_hash:
            report.breaks.append(ChainBreak(row.id, "prev_link"))
        if row.hash != row_hash(row.prev_hash, row.canonical):
            report.breaks.append(ChainBreak(row.id, "content"))
        report.rows += 1
        report.head_id = row.id
        report.head_hash = row.hash
    return report


async def verify_chain(engine: AsyncEngine) -> ChainReport:
    """Verify the whole log, streamed with a server-side cursor in one snapshot."""
    async with engine.connect() as conn:

        async def rows() -> AsyncIterable[ChainRow]:
            result = await conn.stream(_CHAIN_ROWS)
            async for row in result:
                yield ChainRow(row.id, row.prev_hash, row.hash, row.canonical)

        report = await check_chain(rows())
        await conn.rollback()
        return report
