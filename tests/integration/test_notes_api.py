"""Chunk 2.1: private notes API over HTTP. CRUD, BOLA (404 across users), ciphertext at rest."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import Harness, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    async with harness(settings, factory) as h:
        yield h


async def test_note_crud(h: Harness) -> None:
    alice = await h.login()
    c, headers = alice.client, alice.headers

    created = await c.post(
        "/notes",
        json={"title": "Przegląd wiosenny", "body": "Ul nr 3: matka czerwiona"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["title"] == "Przegląd wiosenny"
    assert note["attachments"] == []
    assert note["derivatives"] == []

    listed = (await c.get("/notes")).json()
    assert [n["id"] for n in listed] == [note["id"]]
    assert listed[0]["title"] == "Przegląd wiosenny"
    assert "body" not in listed[0]

    patched = await c.patch(
        f"/notes/{note['id']}", json={"body": "Ul nr 3: wymiana matki"}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "Ul nr 3: wymiana matki"
    assert patched.json()["title"] == "Przegląd wiosenny"

    fetched = (await c.get(f"/notes/{note['id']}")).json()
    assert fetched["body"] == "Ul nr 3: wymiana matki"

    assert (await c.delete(f"/notes/{note['id']}", headers=headers)).status_code == 204
    assert (await c.get(f"/notes/{note['id']}")).status_code == 404
    assert (await c.get("/notes")).json() == []


@pytest.mark.parametrize(
    "payload",
    [{"title": ""}, {"title": "x" * 201}, {"title": "ok", "body": "x" * 200_001}, {}],
    ids=["empty-title", "long-title", "long-body", "missing-title"],
)
async def test_note_validation(h: Harness, payload: dict[str, str]) -> None:
    alice = await h.login()
    response = await alice.client.post("/notes", json=payload, headers=alice.headers)
    assert response.status_code == 422


async def test_empty_patch_is_rejected(h: Harness) -> None:
    alice = await h.login()
    note = (await alice.client.post("/notes", json={"title": "t"}, headers=alice.headers)).json()
    response = await alice.client.patch(f"/notes/{note['id']}", json={}, headers=alice.headers)
    assert response.status_code == 422


async def test_other_users_note_is_404_for_every_verb(h: Harness) -> None:
    """BOLA/IDOR: Bob knows Alice's note id but the database hides the row from him."""
    alice, bob = await h.login(), await h.login()
    note = (
        await alice.client.post(
            "/notes", json={"title": "Tajne", "body": "x"}, headers=alice.headers
        )
    ).json()
    url = f"/notes/{note['id']}"

    assert (await bob.client.get(url)).status_code == 404
    assert (
        await bob.client.patch(url, json={"title": "pwned"}, headers=bob.headers)
    ).status_code == 404
    assert (await bob.client.delete(url, headers=bob.headers)).status_code == 404
    assert (await bob.client.get("/notes")).json() == []
    # Same answer as a note that never existed: no existence oracle.
    missing = await bob.client.get("/notes/00000000-0000-7000-8000-000000000000")
    assert (missing.status_code, missing.json()) == (404, (await bob.client.get(url)).json())

    still = (await alice.client.get(url)).json()
    assert still["title"] == "Tajne"


async def test_notes_require_session_and_csrf(h: Harness) -> None:
    alice = await h.login()
    assert (await alice.client.post("/notes", json={"title": "t"})).status_code == 403  # no CSRF
    anonymous = await h.login()
    await anonymous.client.post("/auth/logout", headers=anonymous.headers)
    assert (await anonymous.client.get("/notes")).status_code == 401


async def test_database_holds_only_ciphertext(h: Harness, migrated: PgInstance) -> None:
    alice = await h.login()
    await alice.client.post(
        "/notes",
        json={"title": "Pasieka Zalesie", "body": "PESEL 44051401359"},
        headers=alice.headers,
    )
    engine = create_async_engine(migrated.superuser_url)  # bypasses RLS: sees raw rows
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT title_ciphertext, body_ciphertext, dek_wrapped FROM private_note "
                        "WHERE owner_id = :id"
                    ),
                    {"id": alice.user_id},
                )
            ).all()
    finally:
        await engine.dispose()
    ((title, body, dek),) = rows
    for blob in (title, body, dek):
        assert b"Pasieka" not in blob
        assert b"44051401359" not in blob
    assert len(dek) > 32  # wrapped, not the raw DEK
