"""``spoldzielnia reindex``: re-embed the text index from Postgres (4.4, 10.3).

Since 10.3 this is the same mechanism as an embedding-model change (``vector.reembed``): a new
index generation is created for the active embedding model (its dimension probed), filled
from Postgres (every CANONICAL article, then every finished derivative of every member, read
as its owner through RLS) and swapped in when complete. The serving generation keeps answering
meanwhile, and pending outbox operations still apply to both; nothing is dropped up front.

The visual atlas (10.6) the same way: a new atlas generation for the configured image model
(the IMAGE_EMBED API model, else the built-in SigLIP), filled with every APPROVED + ACTIVE
photo and swapped in. Skipped, with a log line, when there is no image model.

Runs with the worker's settings: the KEK (private derivatives are decrypted to embed them) and
the embedding model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.generations import GenerationStore
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.reembed import GenerationManager, ReEmbedder

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import LlmSettings, QdrantSettings, WorkerSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import LLMGateway

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    generation: int | None
    items: int
    failed: int
    swapped: bool
    atlas_photos: int = 0


async def rebuild(
    factory: SessionFactory,
    cipher: EnvelopeCipher,
    client: AsyncQdrantClient,
    gateway: LLMGateway,
    llm: LlmSettings,
    qdrant: QdrantSettings,
) -> ReindexReport:
    """Build a new generation with the active embedding model to completion, then swap."""
    store = GenerationStore(factory, cipher, llm, ttl_seconds=0)
    manager = GenerationManager(factory, cipher, client, gateway, llm, qdrant)
    number = await manager.start(None)
    reembed = ReEmbedder(
        factory, store, Indexer(factory, cipher, Embedder(gateway, store), client), manager, llm
    )
    items = 0
    while True:
        run = await reembed.run(budget_seconds=3600.0)
        items += run.processed
        if not run.more:
            break
    failed = await _failed(factory, number)
    report = ReindexReport(number, items, failed, run.swapped)
    log.info(
        "reindex.done",
        generation=number,
        items=items,
        failed=failed,
        swapped=run.swapped,
        waiting=run.waiting,
    )
    return report


async def rebuild_atlas(
    factory: SessionFactory,
    cipher: EnvelopeCipher,
    client: AsyncQdrantClient,
    settings: WorkerSettings,
) -> int:
    """A new atlas generation to completion, then swapped in. Returns the photos indexed."""
    from spoldzielnia_ai.atlas.embedding import SiglipEmbedder
    from spoldzielnia_ai.atlas.generations import (
        AtlasReEmbedder,
        ImageGenerationManager,
        ImageGenerationStore,
    )
    from spoldzielnia_ai.atlas.vectors import AtlasIndexer
    from spoldzielnia_ai.files.storage import S3ObjectStorage
    from spoldzielnia_ai.vector.reembed import GenerationError

    model_dir = Path(settings.models.image_embed_dir)
    siglip = SiglipEmbedder(model_dir) if (model_dir / "model.safetensors").is_file() else None
    store = ImageGenerationStore(factory, cipher, settings.llm, lambda: siglip, ttl_seconds=0)
    manager = ImageGenerationManager(
        factory, client, store, settings.llm, settings.qdrant, lambda: siglip
    )
    try:
        await manager.start(None)
    except GenerationError as exc:
        log.warning("reindex.atlas_skipped", reason=exc.code)
        return 0
    indexer = AtlasIndexer(factory, cipher, S3ObjectStorage(settings.s3), store, client)
    reembed = AtlasReEmbedder(factory, store, indexer, manager, settings.llm)
    photos = 0
    while True:
        run = await reembed.run(budget_seconds=3600.0)
        photos += run.processed
        if not run.more:
            break
    log.info("reindex.atlas_done", photos=photos, swapped=run.swapped)
    return photos


async def _failed(factory: SessionFactory, number: int) -> int:
    from sqlalchemy import select

    from spoldzielnia_ai.db.models import IndexGeneration

    async with factory() as session:
        return int(
            await session.scalar(
                select(IndexGeneration.failed).where(IndexGeneration.number == number)
            )
            or 0
        )


async def run_reindex(settings: WorkerSettings) -> ReindexReport:
    engine = create_app_engine(settings.db)
    client = make_qdrant(settings.qdrant)
    factory = create_session_factory(engine)
    cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    try:
        report = await rebuild(
            factory,
            cipher,
            client,
            LiteLLMGateway(settings.llm, source=LlmConfigStore(factory, cipher, settings.llm)),
            settings.llm,
            settings.qdrant,
        )
        photos = await rebuild_atlas(factory, cipher, client, settings)
        return ReindexReport(report.generation, report.items, report.failed, report.swapped, photos)
    finally:
        await client.close()
        await engine.dispose()
