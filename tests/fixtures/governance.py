"""The app wired to real Postgres and Qdrant for Stage 4 tests. Articles entering CANONICAL are
indexed inline (no Celery), with ``FakeLLMGateway`` embeddings."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from spoldzielnia_ai.notebook.uploads import NoProcessing
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.workers.jobs import ArticleJob
from tests.fixtures.app import harness
from tests.fixtures.llm import EMBED_DIM, FakeLLMGateway

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory
    from tests.fixtures.app import Harness, LoggedIn


class InlineIndexQueue(NoProcessing):
    def __init__(self, indexer: Indexer) -> None:
        self.indexer = indexer
        self.indexed: list[UUID] = []

    async def article_canonical(self, article_id: UUID) -> None:
        self.indexed.append(article_id)
        await self.indexer.index_canonical_article(ArticleJob(article_id=article_id), True)


@asynccontextmanager
async def governance_harness(
    settings: Settings, factory: SessionFactory, qdrant: AsyncQdrantClient, qdrant_url: str
) -> AsyncGenerator[Harness]:
    async with harness(settings, factory) as h:
        h.router.route(url__startswith=qdrant_url).pass_through()  # the real Qdrant container
        h.app.state.qdrant = qdrant
        indexer = Indexer(
            factory, h.app.state.cipher, Embedder(FakeLLMGateway(), EMBED_DIM), qdrant
        )
        h.app.state.processing_queue = InlineIndexQueue(indexer)
        yield h


async def login_expert(h: Harness, *, mfa: bool = True) -> LoggedIn:
    return await h.login(roles=["EXPERT", "MEMBER"], amr=["pwd", "otp"] if mfa else ["pwd"])


async def approve(expert: LoggedIn, article_id: UUID, **extra: object) -> dict[str, object]:
    article = await expert.client.get(f"/wiki/articles/{article_id}")
    assert article.status_code == 200, article.text
    response = await expert.client.post(
        f"/reviews/{article_id}",
        json={"decision": "APPROVE", "version": article.json()["version"], **extra},
        headers=expert.headers,
    )
    assert response.status_code == 200, response.text
    return response.json()
