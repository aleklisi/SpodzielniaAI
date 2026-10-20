"""Operator tooling for the shield, run from the CLI (``spoldzielnia honeypot …``).

Uses the API's settings (the honeypot key is mounted in the ``api`` service) and indexes
injected canaries inline: no worker is needed for them to become retrievable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.shield.honeypot import HoneypotInjector, list_canaries, trace
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync
from spoldzielnia_ai.workers.jobs import ArticleJob

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.core.settings import Settings


async def honeypot_command(
    settings: Settings, action: str, count: int, text: str | None
) -> dict[str, Any] | list[dict[str, Any]]:
    engine = create_app_engine(settings.db)
    factory = create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
    qdrant = make_qdrant(settings.qdrant)
    try:
        if action == "list":
            return [
                {
                    "block_id": str(c.block_id),
                    "signatures": c.signatures,
                    "created_at": c.created_at.isoformat(),
                }
                for c in await list_canaries(factory, settings.shield)
            ]
        if action == "scan":
            signatures, blocks = await trace(factory, settings.shield, text or "")
            return {"signatures": signatures, "honeypot_ids": [str(b) for b in blocks]}
        cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
        source = LlmConfigStore(factory, cipher, settings.llm, redis)
        embedder = Embedder(
            LiteLLMGateway(settings.llm, source=source), settings.qdrant.vector_size
        )
        indexer = Indexer(factory, cipher, embedder, qdrant)

        async def index(article_id: UUID) -> None:
            await indexer.index_canonical_article(ArticleJob(article_id=article_id), True)

        sync = VectorSync(
            factory, CanonicalVectorRepo(qdrant), index, settings.qdrant.timeout_seconds
        )
        injector = HoneypotInjector(factory, embedder, sync, settings.shield, redis)
        result = await injector.inject(count, None)
        return {
            "article_id": str(result.article_id),
            "honeypots": [
                {"block_id": str(c.block_id), "signature": c.signature} for c in result.canaries
            ],
            "index_synced": result.index_synced,
        }
    finally:
        await qdrant.close()
        await redis.aclose()
        await engine.dispose()
