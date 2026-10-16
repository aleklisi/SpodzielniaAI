"""Chunk 2.4: ``POST /notes/{id}/links`` (PLAN Etap 2: SSRF matrix is blocked with 400)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import Harness, LoggedIn, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration


@dataclass
class RecordingQueue:
    links: list[tuple[UUID, UUID]] = field(default_factory=list[tuple[UUID, UUID]])

    async def attachment_stored(self, *_: object) -> list[object]:
        return []

    async def link_added(self, derivative_id: UUID, owner_id: UUID) -> None:
        self.links.append((derivative_id, owner_id))

    async def promotion_requested(self, promotion_id: UUID, owner_id: UUID) -> None:
        return None


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def h(
    settings: Settings, factory: SessionFactory, queue: RecordingQueue
) -> AsyncIterator[Harness]:
    allow_http = settings.model_copy(
        update={"fetch": settings.fetch.model_copy(update={"allow_http": True})}
    )
    async with harness(allow_http, factory) as h:
        h.app.state.processing_queue = queue
        yield h


async def _note(user: LoggedIn) -> str:
    return (await user.client.post("/notes", json={"title": "Linki"}, headers=user.headers)).json()[
        "id"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
        "http://[::ffff:127.0.0.1]",
        "http://2130706433",
        "http://localhost",
        "http://0.0.0.0",
        "http://redis:6379",
        "file:///etc/passwd",
    ],
)
async def test_ssrf_targets_are_blocked(h: Harness, queue: RecordingQueue, url: str) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    response = await alice.client.post(
        f"/notes/{note_id}/links", json={"url": url}, headers=alice.headers
    )
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "ssrf_blocked"
    assert queue.links == []
    assert (await alice.client.get(f"/notes/{note_id}")).json()["derivatives"] == []


async def test_public_link_is_queued_as_pending_derivative(
    h: Harness, queue: RecordingQueue
) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    response = await alice.client.post(
        f"/notes/{note_id}/links",
        json={"url": "https://Pszczelarstwo.example.org/warroza?x=1#frag"},
        headers=alice.headers,
    )
    assert response.status_code == 202, response.text
    derivative = response.json()
    assert derivative["kind"] == "FETCHED_LINK"
    assert derivative["status"] == "PENDING"
    assert derivative["source"] == "https://pszczelarstwo.example.org/warroza?x=1"
    assert derivative["attachment_id"] is None
    assert queue.links == [(UUID(derivative["id"]), UUID(alice.user_id))]

    note = (await alice.client.get(f"/notes/{note_id}")).json()
    assert [d["id"] for d in note["derivatives"]] == [derivative["id"]]


async def test_link_on_other_users_note_is_404(h: Harness, queue: RecordingQueue) -> None:
    alice, bob = await h.login(), await h.login()
    note_id = await _note(alice)
    response = await bob.client.post(
        f"/notes/{note_id}/links", json={"url": "https://example.org/"}, headers=bob.headers
    )
    assert response.status_code == 404
    assert queue.links == []
