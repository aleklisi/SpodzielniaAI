"""``spoldzielnia reindex``: rebuild both collections from Postgres (4.4).

1. Under the index-writer lock, pending outbox rows are marked done: the rebuild reads the
   current state, which already includes their effect. Changes committed after this point
   leave new rows, applied later, in order, as usual.
2. Both collections are dropped and recreated (``qdrant.vector_size``, e.g. after changing
   the embedding model).
3. Every CANONICAL article is indexed, then every finished derivative of every member (read
   as its owner, through RLS).

Runs with the worker's settings: the KEK (private derivatives are decrypted to embed them) and
the local embedding model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.enums import ArticleStatus, DerivativeStatus, OutboxStatus
from spoldzielnia_ai.db.models import NoteDerivative, User, VectorOutbox, WikiArticle
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.collections import recreate_collections
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync
from spoldzielnia_ai.workers.jobs import ArticleJob, DerivativeJob

if TYPE_CHECKING:
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import LLMGateway

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    superseded_ops: int
    articles: int
    derivatives: int
    failed: int


async def _no_reindex(_article_id: UUID) -> None:
    return None


async def rebuild(
    factory: SessionFactory,
    cipher: EnvelopeCipher,
    client: AsyncQdrantClient,
    gateway: LLMGateway,
    vector_size: int,
    timeout_seconds: float = 10.0,
) -> ReindexReport:
    sync = VectorSync(factory, CanonicalVectorRepo(client), _no_reindex, timeout_seconds)
    async with sync.exclusive() as session:
        superseded = len(
            list(
                await session.scalars(
                    update(VectorOutbox)
                    .where(VectorOutbox.status == OutboxStatus.PENDING)
                    .values(status=OutboxStatus.DONE, last_error="superseded_by_reindex")
                    .returning(VectorOutbox.id)
                )
            )
        )
    await recreate_collections(client, vector_size)

    indexer = Indexer(factory, cipher, Embedder(gateway, vector_size), client)
    failed = 0
    async with factory() as session:
        articles = list(
            await session.scalars(
                select(WikiArticle.id).where(WikiArticle.status == ArticleStatus.CANONICAL)
            )
        )
        users = list(await session.scalars(select(User.id)))
    for article_id in articles:
        # final_attempt=False: True back means it could not be indexed now.
        if await indexer.index_canonical_article(ArticleJob(article_id=article_id), False):
            failed += 1
    derivatives = 0
    for user_id in users:
        async with rls_session(factory, user_id) as session:
            ids = list(
                await session.scalars(
                    select(NoteDerivative.id).where(NoteDerivative.status == DerivativeStatus.DONE)
                )
            )
        for derivative_id in ids:
            job = DerivativeJob(derivative_id=derivative_id, owner_id=user_id)
            if await indexer.index_private_derivative(job, False):
                failed += 1
            derivatives += 1
    report = ReindexReport(superseded, len(articles), derivatives, failed)
    log.info(
        "reindex.done",
        superseded_ops=report.superseded_ops,
        articles=report.articles,
        derivatives=report.derivatives,
        failed=report.failed,
    )
    return report


async def run_reindex(settings: WorkerSettings) -> ReindexReport:
    engine = create_app_engine(settings.db)
    client = make_qdrant(settings.qdrant)
    factory = create_session_factory(engine)
    cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    try:
        return await rebuild(
            factory,
            cipher,
            client,
            LiteLLMGateway(settings.llm, source=LlmConfigStore(factory, cipher, settings.llm)),
            settings.qdrant.vector_size,
            settings.qdrant.timeout_seconds,
        )
    finally:
        await client.close()
        await engine.dispose()
