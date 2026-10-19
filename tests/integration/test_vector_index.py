"""Chunk 4.1: Qdrant collections, tenant isolation, the mandatory canonical filter and indexing.

Real Qdrant (testcontainer) and real Postgres. Embeddings come from ``FakeLLMGateway`` (hashed
bag of words), so texts sharing words are neighbours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from qdrant_client import models
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, DerivativeStatus
from spoldzielnia_ai.db.models import NoteDerivative
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.notebook.service import DERIVATIVE, NotebookService
from spoldzielnia_ai.vector.collections import (
    CANONICAL_COLLECTION,
    CANONICAL_INDEXES,
    PRIVATE_COLLECTION,
    VectorSizeMismatch,
    ensure_collections,
)
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.repositories import (
    CanonicalPayload,
    CanonicalPoint,
    CanonicalVectorRepo,
    PrivateChunk,
    PrivateVectorRepo,
)
from spoldzielnia_ai.workers.jobs import ArticleJob, DerivativeJob
from tests.fixtures.llm import EMBED_DIM, FakeLLMGateway, hash_embedding
from tests.fixtures.wiki import make_article, make_user

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

TEXT = "warroza leczenie kwasem szczawiowym w okresie bezczerwiowym"


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def cipher(settings: Settings) -> EnvelopeCipher:
    from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider

    return EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))


def _payload(
    consent: ConsentStatus = ConsentStatus.ACTIVE,
    status: ArticleStatus = ArticleStatus.CANONICAL,
    **overrides: UUID,
) -> CanonicalPayload:
    return CanonicalPayload(
        block_id=overrides.get("block_id", uuid4()),
        article_id=overrides.get("article_id", uuid4()),
        author_id=overrides.get("author_id", uuid4()),
        consent_status=consent,
        article_status=status,
        is_honeypot=False,
        verified_at=None,
    )


async def test_collections_are_idempotent_with_indexes(qdrant: AsyncQdrantClient) -> None:
    assert await ensure_collections(qdrant, EMBED_DIM) == {
        PRIVATE_COLLECTION: False,
        CANONICAL_COLLECTION: False,
    }
    canonical = await qdrant.get_collection(CANONICAL_COLLECTION)
    assert set(CANONICAL_INDEXES) <= set(canonical.payload_schema)
    private = await qdrant.get_collection(PRIVATE_COLLECTION)
    assert private.config.hnsw_config.m == 0  # per-tenant graphs only
    with pytest.raises(VectorSizeMismatch):
        await ensure_collections(qdrant, EMBED_DIM * 2)


async def test_private_search_never_crosses_tenants(qdrant: AsyncQdrantClient) -> None:
    alice, bob = uuid4(), uuid4()
    note, derivative = uuid4(), uuid4()
    vector = hash_embedding(TEXT)
    await PrivateVectorRepo(qdrant, alice).upsert([PrivateChunk(note, derivative, 0, vector)])

    assert [h.note_id for h in await PrivateVectorRepo(qdrant, alice).search(vector)] == [note]
    assert await PrivateVectorRepo(qdrant, bob).search(vector) == []

    # Bob deleting Alice's note id removes nothing: the tenant filter applies to deletes too.
    await PrivateVectorRepo(qdrant, bob).delete_note(note)
    assert len(await PrivateVectorRepo(qdrant, alice).search(vector)) == 1
    await PrivateVectorRepo(qdrant, alice).delete_note(note)
    assert await PrivateVectorRepo(qdrant, alice).search(vector) == []


async def test_canonical_search_returns_only_active_canonical_points(
    qdrant: AsyncQdrantClient,
) -> None:
    repo = CanonicalVectorRepo(qdrant)
    vector = hash_embedding(TEXT)
    active = _payload()
    excluded = [
        _payload(ConsentStatus.SUSPENDED),
        _payload(ConsentStatus.REVOKED),
        _payload(status=ArticleStatus.SUPERSEDED),
        _payload(status=ArticleStatus.DISPUTED),
        _payload(status=ArticleStatus.REVISION_REQUIRED),
        _payload(status=ArticleStatus.IN_REVIEW),
    ]
    await repo.upsert([CanonicalPoint(p, vector) for p in [active, *excluded]])
    # A point whose flags disagree (consent ACTIVE, but deactivated) stays out too.
    inactive = _payload()
    await repo.upsert([CanonicalPoint(inactive, vector)])
    await qdrant.set_payload(CANONICAL_COLLECTION, {"is_active": False}, [str(inactive.block_id)])

    hits = await repo.search(vector, limit=20)
    assert [h.block_id for h in hits] == [active.block_id]

    hostile = models.Filter(
        should=[
            models.FieldCondition(key="consent_status", match=models.MatchValue(value="SUSPENDED")),
            models.FieldCondition(key="is_active", match=models.MatchValue(value=False)),
        ]
    )
    assert await repo.search(vector, limit=20, extra=hostile) == []


async def test_index_canonical_article_upserts_governance_payload(
    factory: SessionFactory, cipher: EnvelopeCipher, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await make_user(factory), await make_user(factory)
    article, blocks = await make_article(
        factory,
        [(alice, TEXT), (bob, "zimowla rodzin pszczelich"), (bob, "miodobranie lipcowe")],
        consents=[ConsentStatus.ACTIVE, ConsentStatus.SUSPENDED, ConsentStatus.ACTIVE],
    )
    llm = FakeLLMGateway()
    indexer = Indexer(factory, cipher, Embedder(llm, EMBED_DIM), qdrant)

    assert await indexer.index_canonical_article(ArticleJob(article_id=article), True) is False

    repo = CanonicalVectorRepo(qdrant)
    stored = {p.id: p.payload for p in await repo.retrieve(blocks)}
    assert set(stored) == set(blocks)  # the suspended block keeps an (inactive) point
    assert stored[blocks[0]]["is_active"] is True
    assert stored[blocks[1]]["is_active"] is False
    assert stored[blocks[1]]["consent_status"] == "SUSPENDED"
    assert stored[blocks[0]]["author_id"] == str(alice)
    assert all("content" not in payload and "text" not in payload for payload in stored.values())
    hits = await repo.search(hash_embedding(TEXT), limit=10)
    assert {h.block_id for h in hits} == {blocks[0], blocks[2]}


async def test_non_canonical_article_is_not_indexed(
    factory: SessionFactory, cipher: EnvelopeCipher, qdrant: AsyncQdrantClient
) -> None:
    alice = await make_user(factory)
    article, blocks = await make_article(factory, [(alice, TEXT)], status=ArticleStatus.DRAFT)
    indexer = Indexer(factory, cipher, Embedder(FakeLLMGateway(), EMBED_DIM), qdrant)

    await indexer.index_canonical_article(ArticleJob(article_id=article), True)

    assert await CanonicalVectorRepo(qdrant).retrieve(blocks) == []


async def test_index_private_derivative_into_the_owners_partition(
    factory: SessionFactory, cipher: EnvelopeCipher, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await make_user(factory), await make_user(factory)
    async with rls_session(factory, alice) as session:
        notebook = NotebookService(session, cipher)
        note = await notebook.create(alice, "Przegląd", "")
        link = await notebook.add_link(note.id, "https://example.org/warroza")
        assert link is not None
        row = await session.get(NoteDerivative, link.id)
        assert row is not None
        aad = row_aad(DERIVATIVE, row.id, row.owner_id)
        dek = cipher.unwrap(row.dek_wrapped, aad)
        long_text = "\n\n".join([TEXT] + [f"akapit {i} " + "ul " * 300 for i in range(6)])
        row.content_ciphertext = cipher.encrypt_field(dek, long_text.encode(), aad, "content")
        row.status = DerivativeStatus.DONE
    llm = FakeLLMGateway()
    indexer = Indexer(factory, cipher, Embedder(llm, EMBED_DIM), qdrant)
    job = DerivativeJob(derivative_id=link.id, owner_id=alice)

    assert await indexer.index_private_derivative(job, True) is False
    assert await indexer.index_private_derivative(job, True) is False  # idempotent

    hits = await PrivateVectorRepo(qdrant, alice).search(hash_embedding(TEXT), limit=50)
    assert len(hits) == len(llm.embedded) // 2 > 1  # chunked; re-index replaced, not added
    assert {h.note_id for h in hits} == {note.id}
    assert hits[0].chunk == 0
    assert await PrivateVectorRepo(qdrant, bob).search(hash_embedding(TEXT)) == []


async def test_private_indexing_retries_while_the_local_model_is_down(
    factory: SessionFactory, cipher: EnvelopeCipher, qdrant: AsyncQdrantClient
) -> None:
    alice = await make_user(factory)
    async with rls_session(factory, alice) as session:
        notebook = NotebookService(session, cipher)
        note = await notebook.create(alice, "Przegląd", "")
        link = await notebook.add_link(note.id, "https://example.org/")
        assert link is not None
        row = await session.get(NoteDerivative, link.id)
        assert row is not None
        aad = row_aad(DERIVATIVE, row.id, row.owner_id)
        row.content_ciphertext = cipher.encrypt_field(
            cipher.unwrap(row.dek_wrapped, aad), TEXT.encode(), aad, "content"
        )
        row.status = DerivativeStatus.DONE
    indexer = Indexer(
        factory, cipher, Embedder(FakeLLMGateway(local_available=False), EMBED_DIM), qdrant
    )
    job = DerivativeJob(derivative_id=link.id, owner_id=alice)

    assert await indexer.index_private_derivative(job, final_attempt=False) is True
    assert await indexer.index_private_derivative(job, final_attempt=True) is False


async def test_active_vectors_skip_inactive_and_honeypot_points(qdrant: AsyncQdrantClient) -> None:
    """The nightly topic clusters (5.3) are built from retrievable, real knowledge only."""
    repo = CanonicalVectorRepo(qdrant)
    active = _payload()
    honeypot = CanonicalPayload(
        block_id=uuid4(),
        article_id=uuid4(),
        author_id=uuid4(),
        consent_status=ConsentStatus.ACTIVE,
        article_status=ArticleStatus.CANONICAL,
        is_honeypot=True,
        verified_at=None,
    )
    vector = hash_embedding(TEXT)
    await repo.upsert(
        [
            CanonicalPoint(active, vector),
            CanonicalPoint(honeypot, hash_embedding("kanarek")),
            CanonicalPoint(_payload(ConsentStatus.SUSPENDED), hash_embedding("wstrzymany")),
        ]
    )

    batches = [batch async for batch in repo.active_vectors(batch=1)]

    assert [v for batch in batches for v in batch] == [pytest.approx(vector)]
