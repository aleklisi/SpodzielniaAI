"""Chunk 1.4: Postgres enforces per-owner isolation of ``private_note`` (PLAN Etap 1 verification).

Every check goes through a real SQLAlchemy session as the runtime role. The database itself must
refuse, and no ``WHERE owner_id`` in Python is involved.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.auth.deps import current_user_id
from spoldzielnia_ai.db.models import PrivateNote, User
from spoldzielnia_ai.db.repositories import PrivateNoteRepository
from spoldzielnia_ai.db.rls import RlsSessionDep, rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture
async def app_engine(migrated: PgInstance) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated.app_url, pool_size=1, max_overflow=0)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(app_engine: AsyncEngine) -> SessionFactory:
    return create_session_factory(app_engine)


async def _new_user(factory: SessionFactory) -> UUID:
    async with factory() as session, session.begin():
        user = User(oidc_issuer="https://idp.test", oidc_subject=secrets.token_hex(8))
        session.add(user)
    return user.id


def _note(owner_id: UUID, title: str = "Przegląd uli") -> PrivateNote:
    return PrivateNote(
        owner_id=owner_id,
        title_ciphertext=title.encode(),
        body_ciphertext=b"ciphertext",
        dek_wrapped=b"dek",
        source_type="text",
    )


@pytest.fixture
async def alice(factory: SessionFactory) -> UUID:
    return await _new_user(factory)


@pytest.fixture
async def bob(factory: SessionFactory) -> UUID:
    return await _new_user(factory)


@pytest.fixture
async def alice_note(factory: SessionFactory, alice: UUID) -> UUID:
    async with rls_session(factory, alice) as session:
        note = await PrivateNoteRepository(session).add(_note(alice))
    return note.id


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


async def test_owner_sees_own_note(factory: SessionFactory, alice: UUID, alice_note: UUID) -> None:
    async with rls_session(factory, alice) as session:
        repo = PrivateNoteRepository(session)
        assert (note := await repo.get(alice_note)) is not None
        assert note.owner_id == alice
        assert [n.id for n in await repo.list_all()] == [alice_note]


async def test_other_user_cannot_read_update_or_delete(
    factory: SessionFactory, alice: UUID, bob: UUID, alice_note: UUID
) -> None:
    async with rls_session(factory, bob) as session:
        repo = PrivateNoteRepository(session)
        assert await repo.get(alice_note) is None
        assert await repo.list_all() == []
        params = {"id": alice_note}
        updated = await session.execute(
            text("UPDATE private_note SET source_type = 'pwned' WHERE id = :id RETURNING id"),
            params,
        )
        assert updated.all() == []
        deleted = await session.execute(
            text("DELETE FROM private_note WHERE id = :id RETURNING id"), params
        )
        assert deleted.all() == []

    async with rls_session(factory, alice) as session:
        note = await PrivateNoteRepository(session).get(alice_note)
        assert note is not None
        assert note.title_ciphertext == "Przegląd uli".encode()


async def test_session_without_user_sees_nothing(factory: SessionFactory, alice_note: UUID) -> None:
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(PrivateNote)) == 0
        assert await session.get(PrivateNote, alice_note) is None


async def test_insert_for_another_owner_is_rejected(
    factory: SessionFactory, alice: UUID, bob: UUID
) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        async with rls_session(factory, bob) as session:
            await PrivateNoteRepository(session).add(_note(alice))
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


async def test_insert_without_user_is_rejected(factory: SessionFactory, alice: UUID) -> None:
    async def insert() -> None:
        async with factory() as session, session.begin():
            session.add(_note(alice))

    with pytest.raises(DBAPIError) as excinfo:
        await insert()
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


async def test_owner_cannot_hand_note_to_another_user(
    factory: SessionFactory, alice: UUID, bob: UUID, alice_note: UUID
) -> None:
    with pytest.raises(DBAPIError) as excinfo:
        async with rls_session(factory, alice) as session:
            await session.execute(
                text("UPDATE private_note SET owner_id = :bob WHERE id = :id"),
                {"bob": bob, "id": alice_note},
            )
    assert _sqlstate(excinfo.value) == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize("fail", [False, True], ids=["commit", "rollback"])
async def test_setting_does_not_leak_to_pooled_connection(
    factory: SessionFactory, app_engine: AsyncEngine, alice: UUID, fail: bool
) -> None:
    """The pool holds one connection, so the follow-up query reuses the RLS one."""
    setting = text("SELECT pg_backend_pid(), current_setting('app.current_user_id', true)")

    class Boom(Exception):
        pass

    pid: int | None = None
    try:
        async with rls_session(factory, alice) as session:
            pid, value = (await session.execute(setting)).one()
            assert value == str(alice)
            if fail:
                raise Boom
    except Boom:
        pass

    async with app_engine.connect() as conn:
        reused_pid, value = (await conn.execute(setting)).one()
    assert reused_pid == pid
    assert value in ("", None)


async def test_force_rls_binds_table_owner(migrated: PgInstance, alice_note: UUID) -> None:
    engine = create_async_engine(migrated.owner_url)
    try:
        async with engine.connect() as conn:
            flags = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = 'private_note'"
                    )
                )
            ).one()
            assert tuple(flags) == (True, True)
            count = await conn.scalar(
                text("SELECT count(*) FROM private_note WHERE id = :id"), {"id": alice_note}
            )
            assert count == 0
    finally:
        await engine.dispose()


async def test_rls_dependency_scopes_requests_to_caller(
    factory: SessionFactory, alice: UUID, bob: UUID, alice_note: UUID
) -> None:
    app = FastAPI()
    app.state.session_factory = factory

    @app.get("/notes")
    async def list_notes(session: RlsSessionDep) -> list[str]:  # pyright: ignore[reportUnusedFunction]
        return [str(n.id) for n in await PrivateNoteRepository(session).list_all()]

    @app.post("/notes", status_code=201)
    async def create_note(session: RlsSessionDep) -> str:  # pyright: ignore[reportUnusedFunction]
        return str((await PrivateNoteRepository(session).add(_note(bob, "nowa"))).id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/notes")).status_code == 401

        app.dependency_overrides[current_user_id] = lambda: alice
        assert (await client.get("/notes")).json() == [str(alice_note)]

        app.dependency_overrides[current_user_id] = lambda: bob
        assert (await client.get("/notes")).json() == []
        created = await client.post("/notes")
        assert created.status_code == 201
        # Committed before the response was sent: visible to the very next request.
        assert (await client.get("/notes")).json() == [created.json()]
