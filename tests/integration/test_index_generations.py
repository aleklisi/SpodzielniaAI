"""Chunk 10.3: versioned text index and safe re-embedding (real Postgres and Qdrant), P8.

The admin's configuration comes from the database (seeded from ``.env``: EMBED → the local
``test-embed``). ``Spaces`` gives each embedding model its own vector space, so a query embedded
with one model and searched in another's collection would find the wrong points.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime in annotations of dict literals

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider, row_aad
from spoldzielnia_ai.core.settings import RagSettings
from spoldzielnia_ai.db.enums import (
    ConsentStatus,
    DerivativeStatus,
    IndexGenerationStatus,
    LlmCapability,
    LlmRoutePurpose,
)
from spoldzielnia_ai.db.models import ArticleBlock, IndexGeneration, LlmProvider, NoteDerivative
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.config_service import LlmConfigService, ModelIn
from spoldzielnia_ai.llm.gateway import LLMPurpose, ModelNotConfigured
from spoldzielnia_ai.notebook.service import DERIVATIVE, NotebookService
from spoldzielnia_ai.rag.private import NotebookQA
from spoldzielnia_ai.rag.service import RAGService
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.generations import GenerationStore
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.reembed import GenerationManager, ReEmbedder
from spoldzielnia_ai.vector.repositories import CanonicalFanout, CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync, suspend_blocks
from spoldzielnia_ai.workers.jobs import ArticleJob, DerivativeJob
from tests.fixtures.llm import FakeLLMGateway, hash_embedding
from tests.fixtures.rag import paraphrase
from tests.fixtures.wiki import make_article, make_user

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.llm.gateway import Deployment

pytestmark = pytest.mark.integration

Status = IndexGenerationStatus
TEXT_A = "warroza leczenie kwasem szczawiowym zimą w okresie bezczerwiowym"
TEXT_B = "warroza monitoring osypu na wkładce dennej przez tydzień"
NEW_MODEL = "other-embed"


class Spaces(FakeLLMGateway):
    """``other-embed`` lives in another vector space than ``test-embed`` (same dimension)."""

    async def embed(
        self,
        texts: list[str],
        *,
        private_data: bool = True,
        purpose: LLMPurpose = LLMPurpose.EMBED,
        deployment: Deployment | None = None,
    ) -> list[list[float]]:
        vectors = await super().embed(
            texts, private_data=private_data, purpose=purpose, deployment=deployment
        )
        if deployment is not None and deployment.model == NEW_MODEL:
            return [_other_space(v) for v in vectors]
        return vectors


def _other_space(vector: list[float]) -> list[float]:
    return vector[::-1]


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


class World:
    def __init__(self, settings: Settings, factory: SessionFactory, qdrant: AsyncQdrantClient):
        self.settings = settings
        self.factory = factory
        self.qdrant = qdrant
        self.cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
        self.llm = Spaces(reply=paraphrase)
        self.config = LlmConfigService(factory, self.cipher, settings.llm)
        self.store = GenerationStore(factory, self.cipher, settings.llm, ttl_seconds=0)
        self.embedder = Embedder(self.llm, self.store)
        self.indexer = Indexer(factory, self.cipher, self.embedder, qdrant)

    def manager(self, grace_hours: int = 24) -> GenerationManager:
        qdrant = self.settings.qdrant.model_copy(update={"retired_grace_hours": grace_hours})
        return GenerationManager(
            self.factory, self.cipher, self.qdrant, self.llm, self.settings.llm, qdrant
        )

    def reembedder(self, manager: GenerationManager | None = None) -> ReEmbedder:
        return ReEmbedder(
            self.factory, self.store, self.indexer, manager or self.manager(), self.settings.llm
        )

    def rag(self) -> RAGService:
        rag = RagSettings.model_validate({"score_threshold": 0.3})
        return RAGService(self.factory, self.qdrant, self.embedder, self.llm, rag)

    def sync(self) -> VectorSync:
        async def no_reindex(_article_id: UUID) -> None:
            return None

        return VectorSync(self.factory, CanonicalFanout(self.qdrant, self.store), no_reindex, 5)

    async def switch_model(self, name: str = NEW_MODEL) -> int:
        """Route EMBED to another local model (probed) and activate it, like an admin."""
        (provider,) = await self.config.providers()
        model = await self.config.create_model(
            None, ModelIn(provider_id=provider.id, model_name=name, capability=LlmCapability.EMBED)
        )
        return await self.route_embed(model.id)

    async def route_embed(self, model_id: UUID) -> int:
        draft = await self.config.create_draft(None, None, None)
        await self.config.set_routing(
            None, draft.revision.number, {LlmRoutePurpose.EMBED: model_id}
        )
        for model in await self.config.models():
            await self.config.record_probe(None, model.id, True, {"checks": []})
        return (await self.config.activate(None, draft.revision.number)).revision.number

    async def generations(self) -> dict[int, IndexGeneration]:
        async with self.factory() as session:
            rows = await session.scalars(select(IndexGeneration))
            return {r.number: r for r in rows}


@pytest.fixture
async def world(settings: Settings, factory: SessionFactory, qdrant: AsyncQdrantClient) -> World:
    w = World(settings, factory, qdrant)
    await w.config.ensure_seeded(None)  # revision 1: EMBED → local/test-embed
    return w


async def _content(w: World) -> tuple[UUID, list[UUID], UUID, UUID]:
    """A canonical article (TEXT_A) and a member's finished derivative (TEXT_B)."""
    alice = await make_user(w.factory)
    article_id, blocks = await make_article(w.factory, [(alice, TEXT_A)])
    async with rls_session(w.factory, alice) as session:
        notebook = NotebookService(session, w.cipher)
        note = await notebook.create(alice, "Przegląd", "")
        link = await notebook.add_link(note.id, "https://example.org/")
        assert link is not None
        row = await session.get(NoteDerivative, link.id)
        assert row is not None
        aad = row_aad(DERIVATIVE, row.id, row.owner_id)
        row.content_ciphertext = w.cipher.encrypt_field(
            w.cipher.unwrap(row.dek_wrapped, aad), TEXT_B.encode(), aad, "content"
        )
        row.status = DerivativeStatus.DONE
        derivative_id = row.id
    await w.indexer.index_canonical_article(ArticleJob(article_id=article_id), True)
    job = DerivativeJob(derivative_id=derivative_id, owner_id=alice)
    await w.indexer.index_private_derivative(job, True)
    return article_id, blocks, alice, note.id


async def test_existing_collections_are_adopted_as_the_first_generation(world: World) -> None:
    reconciled = await world.manager().reconcile()

    assert (reconciled.adopted, reconciled.created, reconciled.building) == (1, None, False)
    (first,) = (await world.generations()).values()
    assert (first.status, first.canonical_collection, first.dimension) == (
        Status.SERVING,
        "canonical_wiki_index",
        32,
    )
    assert first.model == "local/test-embed"
    assert (await world.manager().reconcile()).adopted is None  # idempotent


async def test_queries_use_the_serving_generations_model_until_the_swap(world: World) -> None:
    """P8: a model change builds generation 2 next to generation 1; each query is embedded with
    the model of the generation it searches, before and after the swap."""
    await world.manager().reconcile()
    _, blocks, alice, note_id = await _content(world)
    rag = world.rag()
    assert set(blocks) <= {b.block_id for b in (await rag.ask(TEXT_A)).retrieval.blocks}

    await world.switch_model()
    reconciled = await world.manager().reconcile()
    assert (reconciled.created, reconciled.building) == (2, True)
    building = (await world.generations())[2]
    assert (building.status, building.total, building.canonical_collection) == (
        Status.BUILDING,
        2,
        "canonical_wiki_index__g2",
    )

    # While generation 2 is built, queries still embed with test-embed and search generation 1.
    query = await world.embedder.query([TEXT_A])
    assert (query.generation.number, world.llm.embed_models[-1]) == (1, "test-embed")
    assert set(blocks) <= {b.block_id for b in (await rag.ask(TEXT_A)).retrieval.blocks}

    # One item, then the run stops (time budget); a new run (a restarted worker) resumes.
    first = await world.reembedder().run(budget_seconds=0)
    assert (first.processed, first.more, first.swapped) == (1, True, False)
    saved = (await world.generations())[2]
    assert (saved.done, (saved.cursor or {}).get("phase")) == (1, "articles")
    second = await world.reembedder().run()
    assert (second.processed, second.swapped) == (1, True)

    rows = await world.generations()
    assert (rows[1].status, rows[2].status, rows[2].done, rows[2].failed) == (
        Status.RETIRED,
        Status.SERVING,
        2,
        0,
    )
    assert rows[1].drop_after is not None

    query = await world.embedder.query([TEXT_A])
    assert (query.generation.number, world.llm.embed_models[-1]) == (2, NEW_MODEL)
    assert set(blocks) <= {b.block_id for b in (await rag.ask(TEXT_A)).retrieval.blocks}
    # The new collection holds the new model's vectors only.
    stored = CanonicalVectorRepo(world.qdrant, "canonical_wiki_index__g2")
    hits = await stored.search(_other_space(hash_embedding(TEXT_A)), 5)
    assert hits[0].block_id == blocks[0]
    assert hits[0].score > 0.99
    notes = NotebookQA(world.qdrant, world.embedder, world.llm, RagSettings())
    async with rls_session(world.factory, alice) as session:
        answer = await notes.ask(NotebookService(session, world.cipher), alice, TEXT_B)
    assert [c.note_id for c in answer.citations] == [note_id]
    assert world.llm.embed_models[-1] == NEW_MODEL


async def test_a_consent_change_during_the_build_reaches_the_new_generation(world: World) -> None:
    await world.manager().reconcile()
    _, blocks, alice, _ = await _content(world)
    await world.switch_model()
    await world.manager().reconcile()
    run = await world.reembedder().run(budget_seconds=0)  # the article is in generation 2 now
    assert run.more

    async with world.factory() as session, session.begin():
        seqs = await VectorSync.record(session, [suspend_blocks(alice, None)])
    # The block itself is suspended in Postgres as the consent service would do it.
    async with world.factory() as session, session.begin():
        await session.execute(
            update(ArticleBlock)
            .where(ArticleBlock.author_id == alice)
            .values(consent_status=ConsentStatus.SUSPENDED)
        )
    assert await world.sync().flush(seqs)

    for name in ("canonical_wiki_index", "canonical_wiki_index__g2"):  # both generations
        (point,) = await CanonicalVectorRepo(world.qdrant, name).retrieve(blocks)
        assert (point.payload["consent_status"], point.payload["is_active"]) == ("SUSPENDED", False)
    assert (await world.reembedder().run()).swapped
    assert (await world.rag().ask(TEXT_A)).retrieval.blocks == []


async def test_cancel_before_the_swap_keeps_the_old_generation_serving(world: World) -> None:
    await world.manager().reconcile()
    _, blocks, _, _ = await _content(world)
    await world.switch_model()
    await world.manager().reconcile()

    await world.manager().cancel(2, None)

    rows = await world.generations()
    assert (rows[1].status, rows[2].status, rows[2].dropped_at is not None) == (
        Status.SERVING,
        Status.CANCELLED,
        True,
    )
    assert not await world.qdrant.collection_exists("canonical_wiki_index__g2")
    # Not started again for the same revision; queries keep using generation 1 and its model.
    assert (await world.manager().reconcile()).building is False
    assert (await world.reembedder().run()).generation is None
    assert set(blocks) <= {b.block_id for b in (await world.rag().ask(TEXT_A)).retrieval.blocks}
    assert world.llm.embed_models[-1] == "test-embed"


async def test_going_back_to_the_serving_model_cancels_the_build(world: World) -> None:
    await world.manager().reconcile()
    (original,) = [m for m in await world.config.models() if m.model_name == "test-embed"]
    await world.switch_model()
    await world.manager().reconcile()

    await world.route_embed(original.id)  # the admin rolls the embedding model back
    reconciled = await world.manager().reconcile()

    assert (reconciled.cancelled, reconciled.building) == (2, False)
    assert (await world.generations())[1].status == Status.SERVING


async def test_un_clearing_the_provider_stops_its_generation(world: World) -> None:
    """The serving generation's model is resolved live: an un-cleared provider gives no model,
    and queries are refused instead of being embedded elsewhere."""
    await world.manager().reconcile()
    async with world.factory() as session, session.begin():
        await session.execute(update(LlmProvider).values(private_data_cleared=False))
    embedded = len(world.llm.embedded)

    with pytest.raises(ModelNotConfigured):
        await world.embedder.query(["pytanie"])
    assert len(world.llm.embedded) == embedded  # nothing sent anywhere


async def test_retired_generations_are_dropped_after_the_grace_period(world: World) -> None:
    await world.manager().reconcile()
    await world.switch_model()
    manager = world.manager(grace_hours=0)
    await manager.reconcile()
    assert (await world.reembedder(manager).run()).swapped

    assert await manager.drop_expired() == 1
    rows = await world.generations()
    assert (rows[1].status, rows[1].dropped_at is not None) == (Status.RETIRED, True)
    collections = {c.name for c in (await world.qdrant.get_collections()).collections}
    assert "canonical_wiki_index" not in collections
    # The family name now points at generation 2 (operators' convenience).
    aliases = await world.qdrant.get_aliases()
    pairs = {(a.alias_name, a.collection_name) for a in aliases.aliases}
    assert ("canonical_wiki_index", "canonical_wiki_index__g2") in pairs


async def test_a_first_embedding_model_creates_and_fills_a_generation(
    world: World, qdrant: AsyncQdrantClient
) -> None:
    """No collections from before 10.3 and no generation yet: one is built, then serves."""
    for name in ("private_notes_index", "canonical_wiki_index"):
        await qdrant.delete_collection(name)
    reconciled = await world.manager().reconcile()
    assert (reconciled.created, reconciled.building) == (1, True)
    with pytest.raises(ModelNotConfigured):  # nothing serves until it is complete
        await world.embedder.query(["x"])
    assert (await world.reembedder().run()).swapped
    assert (await world.store.serving()) is not None
