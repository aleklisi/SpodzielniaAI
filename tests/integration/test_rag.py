"""Chunk 5.1: ``RAGService`` and notebook Q&A against real Postgres and Qdrant.

Embeddings are hashed bags of words (``FakeLLMGateway``), so a question sharing words with a
block finds it. Synthesis is a FakeLLM: no model call ever leaves pytest.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider, row_aad
from spoldzielnia_ai.core.settings import RagSettings
from spoldzielnia_ai.db.enums import ConsentStatus, DerivativeStatus, OutboxStatus, Role
from spoldzielnia_ai.db.models import ArticleBlock, NoteDerivative, VectorOutbox
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.notebook.service import DERIVATIVE, NotebookService
from spoldzielnia_ai.vector.collections import CANONICAL_COLLECTION
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync
from spoldzielnia_ai.workers.jobs import DerivativeJob
from tests.fixtures.governance import governance_harness
from tests.fixtures.llm import FakeLLMGateway, hash_embedding, static_generations
from tests.fixtures.rag import canonical, citing, named_user, paraphrase, rag_service, sources

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance, QdrantInstance

pytestmark = pytest.mark.integration

VARROA = "warroza leczenie kwasem szczawiowym zimą w okresie bezczerwiowym rodziny"
SWARM = "rójka nastrój rojowy mateczniki poszerzanie gniazda wiosną"
QUESTION = "Jak wygląda leczenie warroza kwasem szczawiowym zimą?"


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


async def _corpus(factory: SessionFactory, qdrant: AsyncQdrantClient) -> dict[str, UUID]:
    anna = await named_user(factory, "Anna Kowalska")
    expert = await named_user(factory, "dr Jan Nowak", Role.EXPERT)
    varroa, (varroa_block,) = await canonical(
        factory, qdrant, [(anna, VARROA)], title="Leczenie warrozy", expert_id=expert
    )
    swarm, _ = await canonical(factory, qdrant, [(anna, SWARM)], title="Rójka")
    return {"varroa": varroa, "varroa_block": varroa_block, "swarm": swarm, "anna": anna}


async def test_grounded_answer_cites_the_article(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    corpus = await _corpus(factory, qdrant)
    rag, llm = rag_service(factory, qdrant)

    outcome = await rag.ask(QUESTION)
    answer = await rag.answer(outcome)

    assert answer.no_official_position is False
    assert "[1]" in answer.answer_markdown
    assert "[S" not in answer.answer_markdown  # passage labels never reach the client
    citation = answer.citations[0]
    assert citation.article_id == corpus["varroa"]
    assert citation.article_title == "Leczenie warrozy"
    assert citation.authors == ["Anna Kowalska"]
    assert citation.approving_experts == ["dr Jan Nowak"]
    assert citation.last_verified_at is not None
    assert citation.wiki_url.endswith(f"/wiki/{corpus['varroa']}")
    # The model saw the block as untrusted data; the client gets neither its text nor its id.
    assert sources(llm.calls[0])[0][1] == VARROA
    dumped = json.dumps(answer.model_dump(mode="json"), ensure_ascii=False)
    assert VARROA not in dumped
    assert str(corpus["varroa_block"]) not in dumped


async def test_citing_a_source_that_was_not_given_falls_back(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    await _corpus(factory, qdrant)
    rag, llm = rag_service(factory, qdrant, FakeLLMGateway(reply=citing("S9")))

    answer = await rag.answer(await rag.ask(QUESTION))

    assert answer.no_official_position is True
    assert answer.citations == []
    assert len(llm.calls) == 2  # one corrective retry


async def test_nothing_retrieved_means_no_llm_call(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    rag, llm = rag_service(factory, qdrant)

    answer = await rag.answer(await rag.ask(QUESTION))

    assert answer.no_official_position is True
    assert llm.calls == []
    assert llm.embedded == [QUESTION]


async def test_below_threshold_is_no_answer(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    await _corpus(factory, qdrant)
    rag, llm = rag_service(factory, qdrant, score_threshold=0.99)

    assert (await rag.answer(await rag.ask(QUESTION))).no_official_position is True
    assert llm.calls == []


async def test_block_suspended_in_postgres_is_dropped_and_repaired(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    corpus = await _corpus(factory, qdrant)
    block_id = corpus["varroa_block"]
    # Postgres changed, Qdrant didn't hear of it (e.g. the outbox op still waits for retry).
    async with factory() as session, session.begin():
        await session.execute(
            update(ArticleBlock)
            .where(ArticleBlock.id == block_id)
            .values(consent_status=ConsentStatus.SUSPENDED)
        )
    repo = CanonicalVectorRepo(qdrant, CANONICAL_COLLECTION)
    assert block_id in {h.block_id for h in await repo.search(hash_embedding(QUESTION), 10)}
    rag, llm = rag_service(factory, qdrant)

    outcome = await rag.ask(QUESTION)

    assert outcome.retrieval.stale == 1
    assert all(VARROA not in call.text for call in llm.calls)
    async with factory() as session:
        pending = list(
            await session.scalars(
                select(VectorOutbox).where(VectorOutbox.status == OutboxStatus.PENDING)
            )
        )
    assert [row.filter_json["block_ids"] for row in pending] == [[str(block_id)]]
    assert pending[0].payload_json == {
        "consent_status": "SUSPENDED",
        "article_status": "CANONICAL",
        "is_active": False,
    }

    async def no_reindex(_article: UUID) -> None:
        raise AssertionError("not an upsert")

    await VectorSync(factory, repo, no_reindex, 5).drain()
    assert block_id not in {h.block_id for h in await repo.search(hash_embedding(QUESTION), 10)}


async def test_honeypots_never_reach_synthesis(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    anna = await named_user(factory, "Anna Kowalska")
    canary = f"{VARROA} procedura wewnętrzna PSZ-7Q"
    await canonical(factory, qdrant, [(anna, VARROA), (anna, canary)], honeypot=[False, True])
    rag, llm = rag_service(factory, qdrant)

    outcome = await rag.ask(QUESTION)

    assert [b.is_honeypot for b in outcome.retrieval.blocks].count(True) == 1
    assert [b.is_honeypot for b in outcome.admitted] == [False]
    assert all("PSZ-7Q" not in call.text for call in llm.calls)


# --- Notebook Q&A over HTTP ---------------------------------------------------------------------


def _small(settings: Settings, **rag: float) -> Settings:
    """Settings for the fake 32-dim embeddings."""
    return settings.model_copy(
        update={
            "rag": RagSettings.model_validate(rag),
        }
    )


async def test_notebook_qa_answers_from_own_notes_only(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> None:
    settings = _small(settings, score_threshold=0.3)
    cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    async with governance_harness(settings, factory, qdrant, qdrant_server.url) as h:
        llm = FakeLLMGateway(reply=paraphrase)
        h.app.state.llm = llm
        alice, bob = await h.login(), await h.login()
        alice_id = UUID(alice.user_id)
        async with rls_session(factory, alice_id) as session:
            notebook = NotebookService(session, cipher)
            note = await notebook.create(alice_id, "Przegląd jesienny", "")
            link = await notebook.add_link(note.id, "https://example.org/warroza")
            assert link is not None
            row = await session.get(NoteDerivative, link.id)
            assert row is not None
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = cipher.unwrap(row.dek_wrapped, aad)
            row.content_ciphertext = cipher.encrypt_field(dek, VARROA.encode(), aad, "content")
            row.status = DerivativeStatus.DONE
        indexer = Indexer(factory, cipher, Embedder(FakeLLMGateway(), static_generations()), qdrant)
        job = DerivativeJob(derivative_id=link.id, owner_id=alice_id)
        await indexer.index_private_derivative(job, True)

        mine = await alice.client.post(
            "/notes/ask", json={"question": QUESTION}, headers=alice.headers
        )
        theirs = await bob.client.post(
            "/notes/ask", json={"question": QUESTION}, headers=bob.headers
        )

    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["no_answer"] is False
    assert body["citations"] == [{"note_id": str(note.id), "note_title": "Przegląd jesienny"}]
    assert VARROA not in json.dumps(body, ensure_ascii=False)
    assert theirs.json()["no_answer"] is True  # tenant filter: Bob's notebook is empty
    assert [(c.purpose, c.private_data) for c in llm.calls] == [(LLMPurpose.PRIVATE_NOTE, True)]


async def test_notebook_question_length_is_bounded(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> None:
    async with governance_harness(_small(settings), factory, qdrant, qdrant_server.url) as h:
        h.app.state.llm = FakeLLMGateway()
        alice = await h.login()
        too_long = "pszczoły " * 200
        response = await alice.client.post(
            "/notes/ask", json={"question": too_long}, headers=alice.headers
        )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "question_length"
