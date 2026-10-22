"""``spoldzielnia reindex``: re-embed the text index from Postgres (4.4, 10.3).

Since 10.3 this is the same mechanism as an embedding-model change (``vector.reembed``): a new
index generation is created for the active embedding model (its dimension probed), filled
from Postgres (every CANONICAL article, then every finished derivative of every member, read
as its owner through RLS) and swapped in when complete. The serving generation keeps answering
meanwhile, and pending outbox operations still apply to both; nothing is dropped up front.

The atlas collection (9.8) is still recreated and every APPROVED + ACTIVE photo re-embedded
with the local image model, when the models volume has it (10.6 moves it to generations).

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
        model_dir = Path(settings.models.image_embed_dir)
        if not (model_dir / "model.safetensors").is_file():
            log.warning("reindex.atlas_skipped", reason="image model missing", path=str(model_dir))
            return report
        from spoldzielnia_ai.atlas.consistency import rebuild_atlas
        from spoldzielnia_ai.atlas.embedding import SiglipEmbedder
        from spoldzielnia_ai.atlas.vectors import AtlasIndexer, AtlasVectorRepo
        from spoldzielnia_ai.files.storage import S3ObjectStorage

        indexer = AtlasIndexer(
            factory,
            cipher,
            S3ObjectStorage(settings.s3),
            SiglipEmbedder(model_dir),
            AtlasVectorRepo(client),
        )
        photos = await rebuild_atlas(
            factory, client, settings.qdrant.image_vector_size, indexer.index_image
        )
        return ReindexReport(report.generation, report.items, report.failed, report.swapped, photos)
    finally:
        await client.close()
        await engine.dispose()
