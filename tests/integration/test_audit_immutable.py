"""Chunk 1.4: the security audit log is append-only and its hash chain exposes tampering (G10)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.audit.verify import (
    CANONICAL_ROW_SQL,
    GENESIS_HASH,
    ChainBreak,
    row_hash,
    verify_chain,
)
from spoldzielnia_ai.cli import main
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"


async def _as_superuser(pg: PgInstance, statement: str, **params: object) -> None:
    """Run ``statement`` with triggers disabled, like an attacker with superuser access."""
    engine = create_async_engine(pg.superuser_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL session_replication_role = replica"))
            await conn.execute(text(statement), params)
    finally:
        await engine.dispose()


@pytest.fixture
def audit_db(migrated: PgInstance) -> PgInstance:
    """A migrated database whose audit log starts empty (tests share one container)."""
    asyncio.run(_as_superuser(migrated, "TRUNCATE security_audit_log"))
    return migrated


@pytest.fixture
async def app_engine(audit_db: PgInstance) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(audit_db.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(app_engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(app_engine)


async def _record(factory: SessionFactory, n: int = 1) -> list[int]:
    ids: list[int] = []
    async with factory() as session, session.begin():
        audit = AuditService(session)
        for i in range(n):
            entry = await audit.record(
                EventType.ARTICLE_STATUS_CHANGED,
                uuid7(),
                AuditTarget("wiki_article", str(uuid7())),
                {"from": "DRAFT", "to": "IN_REVIEW", "i": i, "note": "zażółć gęślą jaźń"},
            )
            ids.append(entry.id)
    return ids


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


async def test_entries_form_a_valid_chain(factory: SessionFactory, app_engine: AsyncEngine) -> None:
    ids = await _record(factory, 3)
    ids += await _record(factory, 2)

    report = await verify_chain(app_engine)
    assert report.ok, report.breaks
    assert report.rows == 5
    assert report.head_id == ids[-1]
    assert ids == sorted(ids)

    async with app_engine.connect() as conn:
        first_prev = await conn.scalar(
            text("SELECT prev_hash FROM security_audit_log ORDER BY id LIMIT 1")
        )
        head_hash = await conn.scalar(
            text("SELECT hash FROM security_audit_log WHERE id = :id"), {"id": ids[-1]}
        )
    assert first_prev == GENESIS_HASH
    assert head_hash == report.head_hash


async def test_empty_log_verifies(app_engine: AsyncEngine) -> None:
    report = await verify_chain(app_engine)
    assert report.ok
    assert report.rows == 0
    assert report.head_id is None


async def test_caller_cannot_backdate_or_forge_links(
    factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO security_audit_log (event_type, occurred_at, prev_hash, hash) "
                "VALUES ('USER_LOGIN', '2000-01-01T00:00:00Z', '\\x00', '\\x00')"
            )
        )
    async with app_engine.connect() as conn:
        occurred_at, prev_hash, hash_ = (
            await conn.execute(text("SELECT occurred_at, prev_hash, hash FROM security_audit_log"))
        ).one()
    assert datetime.now(UTC) - occurred_at < timedelta(minutes=5)
    assert prev_hash == GENESIS_HASH
    assert len(hash_) == 32
    assert (await verify_chain(app_engine)).ok


async def test_concurrent_appends_stay_linear(
    factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    await asyncio.gather(*(_record(factory, 2) for _ in range(10)))
    report = await verify_chain(app_engine)
    assert report.ok, report.breaks
    assert report.rows == 20


async def test_rolled_back_append_leaves_chain_intact(
    factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    await _record(factory)
    async with factory() as session:
        await session.begin()
        await AuditService(session).record(EventType.USER_LOGIN, uuid7())
        await session.rollback()
    await _record(factory)

    report = await verify_chain(app_engine)
    assert report.ok, report.breaks
    assert report.rows == 2


async def test_append_outside_read_committed_is_rejected(app_engine: AsyncEngine) -> None:
    engine = app_engine.execution_options(isolation_level="REPEATABLE READ")
    with pytest.raises(DBAPIError, match="READ COMMITTED"):
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO security_audit_log (event_type) VALUES ('X')"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE security_audit_log SET payload = '{}'",
        "DELETE FROM security_audit_log",
        "TRUNCATE security_audit_log",
    ],
)
@pytest.mark.parametrize("role", ["app", "owner"])
async def test_log_cannot_be_modified(
    audit_db: PgInstance, factory: SessionFactory, role: str, statement: str
) -> None:
    """The app role lacks the privilege. The owner has it but is stopped by the trigger."""
    await _record(factory)
    engine = create_async_engine(audit_db.app_url if role == "app" else audit_db.owner_url)
    try:
        with pytest.raises(DBAPIError) as excinfo:
            async with engine.begin() as conn:
                await conn.execute(text(statement))
    finally:
        await engine.dispose()
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE
    if role == "owner":
        assert "append-only" in str(excinfo.value)


async def test_edited_row_is_detected(
    audit_db: PgInstance, factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    ids = await _record(factory, 4)
    await _as_superuser(
        audit_db,
        'UPDATE security_audit_log SET payload = \'{"to": "CANONICAL"}\' WHERE id = :id',
        id=ids[1],
    )
    report = await verify_chain(app_engine)
    assert report.breaks == [ChainBreak(ids[1], "content")]


async def test_edited_and_rehashed_row_breaks_next_link(
    audit_db: PgInstance, factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    ids = await _record(factory, 4)
    await _as_superuser(
        audit_db, "UPDATE security_audit_log SET target_id = 'other' WHERE id = :id", id=ids[1]
    )
    async with app_engine.connect() as conn:
        prev_hash, canonical = (
            await conn.execute(
                text(
                    f"SELECT prev_hash, {CANONICAL_ROW_SQL} "  # noqa: S608 - constant SQL
                    "FROM security_audit_log WHERE id = :id"
                ),
                {"id": ids[1]},
            )
        ).one()
    forged = row_hash(prev_hash, canonical)
    await _as_superuser(
        audit_db,
        "UPDATE security_audit_log SET hash = :hash WHERE id = :id",
        hash=forged,
        id=ids[1],
    )
    report = await verify_chain(app_engine)
    assert report.breaks == [ChainBreak(ids[2], "prev_link")]


async def test_deleted_row_is_detected(
    audit_db: PgInstance, factory: SessionFactory, app_engine: AsyncEngine
) -> None:
    ids = await _record(factory, 4)
    await _as_superuser(audit_db, "DELETE FROM security_audit_log WHERE id = :id", id=ids[1])
    report = await verify_chain(app_engine)
    assert report.breaks == [ChainBreak(ids[2], "prev_link")]
    assert report.rows == 3


def test_audit_verify_cli_exit_code(audit_db: PgInstance, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOLDZIELNIA_DB__HOST", audit_db.host)
    monkeypatch.setenv("SPOLDZIELNIA_DB__PORT", str(audit_db.port))
    monkeypatch.setenv("SPOLDZIELNIA_DB__APP_PASSWORD", audit_db.passwords["pg_app_password"])

    async def record() -> list[int]:
        engine = create_async_engine(audit_db.app_url)
        try:
            return await _record(create_session_factory(engine), 3)
        finally:
            await engine.dispose()

    ids = asyncio.run(record())
    assert main(["audit-verify"]) == 0

    asyncio.run(_as_superuser(audit_db, "DELETE FROM security_audit_log WHERE id = :id", id=ids[0]))
    assert main(["audit-verify"]) == 1


async def test_audit_actor_is_not_a_foreign_key(factory: SessionFactory) -> None:
    """Audit rows outlive users: an unknown actor id is accepted."""
    async with factory() as session, session.begin():
        entry = await AuditService(session).record(
            EventType.USER_PROVISIONED, UUID(int=0), AuditTarget("app_user", "gone")
        )
    assert entry.id > 0
