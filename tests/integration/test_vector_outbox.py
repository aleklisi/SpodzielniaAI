"""Chunk 4.4: outbox, reconciliation, drift audit and reindex (real Postgres and Qdrant).

Qdrant outages are simulated by giving the API a client pointed at a closed port.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider, row_aad
from spoldzielnia_ai.core.settings import QdrantSettings
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, DerivativeStatus, OutboxStatus
from spoldzielnia_ai.db.models import NoteDerivative, SecurityAuditLog, VectorOutbox, WikiArticle
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.notebook.service import DERIVATIVE, NotebookService
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.consistency import ConsistencyAuditor
from spoldzielnia_ai.vector.reindex import rebuild
from spoldzielnia_ai.vector.repositories import (
    CanonicalPayload,
    CanonicalPoint,
    CanonicalScope,
    CanonicalVectorRepo,
    PrivateVectorRepo,
)
from spoldzielnia_ai.vector.revalidate import revalidate
from spoldzielnia_ai.vector.sync import VectorSync, resume_blocks, suspend_blocks
from tests.fixtures.governance import approve, governance_harness, login_expert
from tests.fixtures.llm import EMBED_DIM, FakeLLMGateway, hash_embedding
from tests.fixtures.wiki import make_article, make_user

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn
    from tests.integration.conftest import PgInstance, QdrantInstance

pytestmark = pytest.mark.integration

TEXT_A = "warroza leczenie kwasem szczawiowym zimą w okresie bezczerwiowym"
TEXT_B = "warroza monitoring osypu na wkładce dennej przez tydzień"


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> AsyncIterator[Harness]:
    async with governance_harness(settings, factory, qdrant, qdrant_server.url) as h:
        yield h


@pytest.fixture
async def dead_qdrant() -> AsyncIterator[AsyncQdrantClient]:
    """A client for a Qdrant that is down (nothing listens on the port)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        client = make_qdrant(
            QdrantSettings(url="http://127.0.0.1:9", api_key=SecretStr("x"), timeout_seconds=2)
        )
    yield client
    await client.close()


async def _noop(_article_id: UUID) -> None:
    return None


def _sync(factory: SessionFactory, client: AsyncQdrantClient, timeout: float = 5) -> VectorSync:
    return VectorSync(factory, CanonicalVectorRepo(client), _noop, timeout)


async def _search(qdrant: AsyncQdrantClient, factory: SessionFactory, text: str) -> set[UUID]:
    """Canonical search as retrieval will do it: Qdrant, then re-validation in Postgres."""
    hits = await CanonicalVectorRepo(qdrant).search(hash_embedding(text), 50)
    async with factory() as session:
        return {h.block_id for h in await revalidate(session, hits)}


async def _raw(qdrant: AsyncQdrantClient, text: str) -> set[UUID]:
    return {h.block_id for h in await CanonicalVectorRepo(qdrant).search(hash_embedding(text), 50)}


async def _pending(factory: SessionFactory) -> list[VectorOutbox]:
    async with factory() as session:
        rows = await session.scalars(
            select(VectorOutbox)
            .where(VectorOutbox.status == OutboxStatus.PENDING)
            .order_by(VectorOutbox.seq)
        )
        return list(rows)


async def _time_passes(factory: SessionFactory) -> None:
    """Every pending row's backoff has elapsed."""
    async with factory() as session, session.begin():
        await session.execute(
            update(VectorOutbox)
            .where(VectorOutbox.status == OutboxStatus.PENDING)
            .values(next_attempt_at=VectorOutbox.created_at)
        )


async def _canonical(
    h: Harness, factory: SessionFactory, blocks: list[tuple[LoggedIn, str]]
) -> tuple[UUID, list[UUID]]:
    article_id, block_ids = await make_article(
        factory, [(UUID(u.user_id), t) for u, t in blocks], status=ArticleStatus.IN_REVIEW
    )
    await approve(await login_expert(h), article_id)
    return article_id, block_ids


async def test_revoke_during_qdrant_outage_is_reconciled_later(
    h: Harness,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    dead_qdrant: AsyncQdrantClient,
) -> None:
    alice, bob = await h.login(), await h.login()
    _, (a_block, b_block) = await _canonical(h, factory, [(alice, TEXT_A), (bob, TEXT_B)])
    assert await _search(qdrant, factory, TEXT_B) == {a_block, b_block}
    h.app.state.qdrant = dead_qdrant  # Qdrant goes down

    response = await bob.client.post(
        "/me/contributions/revoke", json={"confirm": "REVOKE"}, headers=bob.headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["index_synced"] is False
    # Qdrant still holds the stale points, but retrieval re-validates against Postgres.
    assert {a_block, b_block} <= await _raw(qdrant, TEXT_B)
    assert await _search(qdrant, factory, TEXT_B) == set()
    pending = await _pending(factory)
    assert [p.op.value for p in pending] == ["DELETE", "SET_PAYLOAD"]
    assert (pending[0].attempts, pending[0].last_error) == (1, "ResponseHandlingException")
    assert pending[1].attempts == 0  # never tried: the one before it failed

    # Qdrant is back. The reconciler waits for the backoff, then applies both, in order.
    sync = _sync(factory, qdrant)
    assert (await sync.reconcile()).applied == 0
    await _time_passes(factory)
    assert (await sync.reconcile()).applied == 2

    assert await _pending(factory) == []
    assert await CanonicalVectorRepo(qdrant).retrieve([b_block]) == []
    assert await _raw(qdrant, TEXT_B) | await _raw(qdrant, TEXT_A) == set()


async def test_outbox_applies_in_commit_order_and_stops_at_failures(
    factory: SessionFactory, qdrant: AsyncQdrantClient, dead_qdrant: AsyncQdrantClient
) -> None:
    author = await make_user(factory)
    article_id, (block,) = await make_article(factory, [(author, TEXT_A)])
    payload = CanonicalPayload(
        block, article_id, author, ConsentStatus.ACTIVE, ArticleStatus.CANONICAL, False, None
    )
    await CanonicalVectorRepo(qdrant).upsert([CanonicalPoint(payload, hash_embedding(TEXT_A))])
    down = _sync(factory, dead_qdrant)
    for op in (suspend_blocks(author, None), resume_blocks(author, (article_id,), activate=True)):
        async with factory() as session, session.begin():
            seqs = await down.record(session, [op])
        assert await down.flush(seqs) is False  # Qdrant down: nothing applied, order kept

    await _time_passes(factory)
    assert (await _sync(factory, qdrant).reconcile()).applied == 2

    (point,) = await CanonicalVectorRepo(qdrant).retrieve([block])
    assert (point.payload["consent_status"], point.payload["is_active"]) == ("ACTIVE", True)


async def test_flush_gives_up_while_another_writer_holds_the_lock(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    author = await make_user(factory)
    holder = _sync(factory, qdrant)
    waiter = _sync(factory, qdrant, timeout=0.3)
    async with factory() as session, session.begin():
        seqs = await waiter.record(session, [suspend_blocks(author, None)])

    async with holder.exclusive():
        assert await asyncio.wait_for(waiter.flush(seqs), timeout=5) is False

    assert await waiter.flush(seqs) is True


async def test_drift_audit_fixes_stale_and_orphaned_points(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await h.login(), await h.login()
    article_id, (a_block, b_block) = await _canonical(h, factory, [(alice, TEXT_A), (bob, TEXT_B)])
    repo = CanonicalVectorRepo(qdrant)
    # Inject drift: A's point claims SUSPENDED/inactive (it is ACTIVE), B's claims to be in
    # a CANONICAL article that Postgres says is DISPUTED, and a point with no block at all.
    await repo.set_payload(
        CanonicalScope(block_ids=(a_block,)), {"consent_status": "SUSPENDED", "is_active": False}
    )
    async with factory() as session, session.begin():
        article = await session.get(WikiArticle, article_id)
        assert article is not None
        article.status = ArticleStatus.DISPUTED  # changed without the index (simulated bug)
    orphan = CanonicalPayload(
        uuid4(), uuid4(), uuid4(), ConsentStatus.ACTIVE, ArticleStatus.CANONICAL, False, None
    )
    await repo.upsert([CanonicalPoint(orphan, hash_embedding(TEXT_B))])
    assert orphan.block_id in await _raw(qdrant, TEXT_B)

    auditor = ConsistencyAuditor(factory, qdrant, _sync(factory, qdrant))
    report = await auditor.run()

    assert (report.deleted, report.fixed) == (1, 2)
    assert await _raw(qdrant, TEXT_A) | await _raw(qdrant, TEXT_B) == set()
    stored = {p.id: p.payload for p in await repo.retrieve([a_block, b_block, orphan.block_id])}
    assert set(stored) == {a_block, b_block}
    assert stored[a_block]["consent_status"] == "ACTIVE"
    assert {p["article_status"] for p in stored.values()} == {"DISPUTED"}
    assert (await auditor.run()).drifted is False
    async with factory() as session:
        events = list(
            await session.scalars(
                select(SecurityAuditLog).where(
                    SecurityAuditLog.event_type == "VECTOR_DRIFT_DETECTED"
                )
            )
        )
    assert events[-1].payload | {"deleted": 1, "fixed": 2} == events[-1].payload


async def test_reindex_rebuilds_both_collections(
    settings: Settings, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    alice = await make_user(factory)
    _, blocks = await make_article(factory, [(alice, TEXT_A)])
    await make_article(factory, [(alice, TEXT_B)], status=ArticleStatus.DRAFT)
    async with rls_session(factory, alice) as session:
        notebook = NotebookService(session, cipher)
        note = await notebook.create(alice, "Przegląd", "")
        link = await notebook.add_link(note.id, "https://example.org/")
        assert link is not None
        row = await session.get(NoteDerivative, link.id)
        assert row is not None
        aad = row_aad(DERIVATIVE, row.id, row.owner_id)
        row.content_ciphertext = cipher.encrypt_field(
            cipher.unwrap(row.dek_wrapped, aad), TEXT_B.encode(), aad, "content"
        )
        row.status = DerivativeStatus.DONE
    async with factory() as session, session.begin():
        await VectorSync.record(session, [suspend_blocks(alice, None)])  # obsolete after rebuild

    report = await rebuild(factory, cipher, qdrant, FakeLLMGateway(), EMBED_DIM)

    assert report.failed == 0
    assert report.superseded_ops >= 1
    assert set(blocks) <= await _raw(qdrant, TEXT_A)
    hits = await PrivateVectorRepo(qdrant, alice).search(hash_embedding(TEXT_B))
    assert [h.note_id for h in hits] == [note.id]
    assert await _pending(factory) == []
