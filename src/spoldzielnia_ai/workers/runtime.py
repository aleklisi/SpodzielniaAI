"""Per-process worker runtime: one event loop, DB engine, clients and engines per process.

Celery's prefork pool forks children after the app is built. Everything with sockets or
threads is therefore created lazily, inside the child, on the first task.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.atlas.recognition import AtlasRecognizer
from spoldzielnia_ai.atlas.submissions import AtlasPreparer
from spoldzielnia_ai.atlas.vectors import AtlasIndexer, AtlasVectorRepo
from spoldzielnia_ai.auth.deps import DbAuditSink
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.files.antivirus import ClamdScanner
from spoldzielnia_ai.files.storage import S3ObjectStorage
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway, StaticConfig
from spoldzielnia_ai.llm.injection_guard import InjectionGuard
from spoldzielnia_ai.net.safe_fetch import SafeFetcher
from spoldzielnia_ai.notebook.queue import CeleryProcessingQueue
from spoldzielnia_ai.notebook.uploads import UploadPipeline
from spoldzielnia_ai.privacy.scrubber import ChecksumAnalyzer, CombinedAnalyzer, PiiScrubber
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.consistency import ConsistencyAuditor
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.generations import GenerationStore
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.reembed import GenerationManager, ReEmbedder
from spoldzielnia_ai.vector.repositories import CanonicalFanout, CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import AtlasSink, VectorSync
from spoldzielnia_ai.wiki.promotion import PromotionProcessor
from spoldzielnia_ai.workers.celery_adapter import make_celery, register
from spoldzielnia_ai.workers.engines.text import TextEngine
from spoldzielnia_ai.workers.engines.vision import GatewayVisionEngine, UnavailableVisionEngine
from spoldzielnia_ai.workers.jobs import MaintenanceJob, TaskName
from spoldzielnia_ai.workers.processing import DerivativeProcessor
from spoldzielnia_ai.workers.scan import DerivativeScanner

if TYPE_CHECKING:
    from collections.abc import Callable

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.atlas.embedding import SiglipEmbedder
    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.injection_guard import Classifier
    from spoldzielnia_ai.workers.engines.base import Engine
    from spoldzielnia_ai.workers.jobs import (
        ArticleJob,
        AtlasIndexJob,
        AtlasJob,
        DerivativeJob,
        ExportJob,
        PromotionJob,
    )

log = get_logger(__name__)

MAX_RETRIES = 3
_TEXT_TYPES = frozenset({"text/plain", "text/markdown"})


class _State:
    """Mimics ``app.state`` for ``DbAuditSink`` and ``UploadPipeline``."""

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory


class WorkerRuntime:
    def __init__(self, settings: WorkerSettings, celery: Any) -> None:
        self._settings = settings
        self._celery = celery
        self._runner: asyncio.Runner | None = None
        self._processor: DerivativeProcessor | None = None
        self._scanner: DerivativeScanner | None = None
        self._promoter: PromotionProcessor | None = None
        self._atlas: AtlasPreparer | None = None
        self._atlas_index: AtlasIndexer | None = None
        self._recognizer: AtlasRecognizer | None = None
        self._embedder: SiglipEmbedder | None = None
        self._indexer: Indexer | None = None
        self._sync: VectorSync | None = None
        self._qdrant_client: AsyncQdrantClient | None = None
        self._guard: tuple[InjectionGuard, bool] | None = None
        self._factory: SessionFactory | None = None
        self._cipher: EnvelopeCipher | None = None
        self._llm_config: LlmConfigStore | None = None
        self._generation_store: GenerationStore | None = None
        self._engine_cache: dict[str, Engine] = {}

    def _engine_for(self, kind: DerivativeKind, mime_type: str) -> Engine:
        models = self._settings.models
        if kind == DerivativeKind.TRANSCRIPT:
            key = "whisper"
            if key not in self._engine_cache:
                from spoldzielnia_ai.workers.engines.whisper import WhisperEngine

                self._engine_cache[key] = WhisperEngine(models)
        elif kind == DerivativeKind.PARSED_DOC and mime_type in _TEXT_TYPES:
            key = "text"
            self._engine_cache.setdefault(key, TextEngine())
        elif kind == DerivativeKind.PARSED_DOC:
            key = "docling"
            if key not in self._engine_cache:
                from spoldzielnia_ai.workers.engines.docling_engine import DoclingEngine

                self._engine_cache[key] = DoclingEngine(models)
        else:
            key = "vision"
            if key not in self._engine_cache:
                # The engine runs on its own event loop (worker thread), so it gets the
                # configuration ``run`` refreshed, not the store with its loop-bound clients.
                store = self._llm_store()
                self._engine_cache[key] = (
                    GatewayVisionEngine(
                        lambda: LiteLLMGateway(
                            self._settings.llm, source=StaticConfig(store.cached)
                        )
                    )
                    if self._settings.llm.vlm_model or self._settings.llm.config_source == "db"
                    else UnavailableVisionEngine()
                )
        return self._engine_cache[key]

    def _llm_store(self) -> LlmConfigStore:
        """One per process: the ACTIVE LLM configuration revision (7.2)."""
        if self._llm_config is None:
            from redis.asyncio import Redis

            factory, cipher = self._shared()
            redis: Redis = Redis.from_url(self._settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
            self._llm_config = LlmConfigStore(factory, cipher, self._settings.llm, redis)
        return self._llm_config

    def _gateway(self) -> LiteLLMGateway:
        return LiteLLMGateway(self._settings.llm, source=self._llm_store())

    def _shared(self) -> tuple[SessionFactory, EnvelopeCipher]:
        if self._factory is None or self._cipher is None:
            settings = self._settings
            self._factory = create_session_factory(create_app_engine(settings.db))
            self._cipher = EnvelopeCipher(
                StaticKeyProvider(settings.security.kek.get_secret_value())
            )
        return self._factory, self._cipher

    def _build_scanner(self) -> DerivativeScanner:
        """worker-core: heuristics always; the classifier from the models volume. Without it,
        the scan still runs and records ``classifier: unavailable``."""
        factory, cipher = self._shared()
        guard, has_classifier = self._injection_guard()
        queue = CeleryProcessingQueue(factory, cipher, self._celery)
        return DerivativeScanner(
            factory, cipher, guard, has_classifier, on_clean=queue.derivative_clean
        )

    def _injection_guard(self) -> tuple[InjectionGuard, bool]:
        """One classifier per process, shared by screening and promotion."""
        if self._guard is None:
            model_dir = Path(self._settings.models.injection_dir)
            classifier: Classifier | None = None
            if (model_dir / "model.safetensors").is_file():
                from spoldzielnia_ai.llm.injection_model import InjectionClassifier

                classifier = InjectionClassifier(model_dir)
            else:
                log.error("worker.injection_classifier_missing", path=str(model_dir))
            self._guard = (InjectionGuard(classifier), classifier is not None)
        return self._guard

    def _build_promoter(self) -> PromotionProcessor:
        """worker-core: Presidio (+ checksum IDs), the guard and the local LLM gateway."""
        from spoldzielnia_ai.privacy.presidio_adapter import PresidioAnalyzer

        factory, cipher = self._shared()
        scrubber = PiiScrubber(CombinedAnalyzer(PresidioAnalyzer.shared(), ChecksumAnalyzer()))
        guard, _ = self._injection_guard()
        return PromotionProcessor(factory, cipher, scrubber, guard, self._gateway())

    def _build_atlas(self) -> AtlasPreparer:
        """worker-core: storage, Presidio, the guard and the local VLM (9.4)."""
        from spoldzielnia_ai.privacy.presidio_adapter import PresidioAnalyzer

        factory, cipher = self._shared()
        settings = self._settings
        scrubber = PiiScrubber(CombinedAnalyzer(PresidioAnalyzer.shared(), ChecksumAnalyzer()))
        guard, _ = self._injection_guard()
        return AtlasPreparer(
            factory,
            cipher,
            S3ObjectStorage(settings.s3),
            scrubber,
            guard,
            self._gateway(),
            settings.atlas,
            max_pixels=settings.uploads.max_image_pixels,
            tmp_dir=settings.uploads.tmp_dir,
        )

    def _qdrant(self) -> AsyncQdrantClient:
        if self._qdrant_client is None:
            self._qdrant_client = make_qdrant(self._settings.qdrant)
        return self._qdrant_client

    def _generations(self) -> GenerationStore:
        """One per process: the text index generations and their models (10.3)."""
        if self._generation_store is None:
            factory, cipher = self._shared()
            self._generation_store = GenerationStore(factory, cipher, self._settings.llm)
        return self._generation_store

    def _build_indexer(self) -> Indexer:
        """worker-core: embeddings per index generation and Qdrant over internal TLS (4.1)."""
        factory, cipher = self._shared()
        embedder = Embedder(self._gateway(), self._generations())
        return Indexer(factory, cipher, embedder, self._qdrant())

    def _build_sync(self) -> VectorSync:
        """worker-core: the outbox reconciler (4.4). UPSERT ops go back to the index queue."""
        factory, cipher = self._shared()
        settings = self._settings
        queue = CeleryProcessingQueue(factory, cipher, self._celery)
        return VectorSync(
            factory,
            CanonicalFanout(self._qdrant(), self._generations()),
            queue.article_canonical,
            settings.qdrant.timeout_seconds,
            settings.governance.outbox_alert_after,
            atlas=AtlasSink(AtlasVectorRepo(self._qdrant()), queue.atlas_canonical),
        )

    def _build(self) -> DerivativeProcessor:
        settings = self._settings
        factory, cipher = self._shared()
        storage = S3ObjectStorage(settings.s3)
        queue = CeleryProcessingQueue(factory, cipher, self._celery)
        uploads = UploadPipeline(
            factory,
            cipher,
            storage,
            ClamdScanner(settings.clamav),
            settings.uploads,
            DbAuditSink(_State(factory)),
            queue,
        )
        return DerivativeProcessor(
            factory,
            cipher,
            storage,
            self._engine_for,
            tmp_dir=settings.uploads.tmp_dir,
            fetcher=SafeFetcher(settings.fetch),
            uploads=uploads,
            on_done=queue.derivative_done,
        )

    def _loop(self) -> asyncio.Runner:
        if self._runner is None:
            self._runner = asyncio.Runner()
        return self._runner

    def run(self, job: DerivativeJob, attempt: int) -> bool:
        runner = self._loop()
        if self._processor is None:
            self._processor = runner.run(_construct(self._build))
        processor = self._processor
        # A fresh LLM configuration for the vision engine (cheap: TTL and a Redis GET).
        runner.run(self._llm_store().current())
        try:
            return runner.run(processor.handle(job, final_attempt=attempt >= MAX_RETRIES))
        except _soft_time_limit():
            runner.run(processor.fail_timeout(job))
            return False

    def scan(self, job: DerivativeJob, attempt: int) -> bool:
        runner = self._loop()
        if self._scanner is None:
            self._scanner = runner.run(_construct(self._build_scanner))
        return runner.run(self._scanner.handle(job, final_attempt=attempt >= MAX_RETRIES))

    def promote(self, job: PromotionJob, attempt: int) -> bool:
        runner = self._loop()
        if self._promoter is None:
            self._promoter = runner.run(_construct(self._build_promoter))
        return runner.run(self._promoter.handle(job, final_attempt=attempt >= MAX_RETRIES))

    def prepare_atlas(self, job: AtlasJob, attempt: int) -> bool:
        runner = self._loop()
        if self._atlas is None:
            self._atlas = runner.run(_construct(self._build_atlas))
        runner.run(self._llm_store().current())
        return runner.run(self._atlas.handle(job, final_attempt=attempt >= MAX_RETRIES))

    def export_atlas(self, job: ExportJob, _attempt: int) -> bool:
        from spoldzielnia_ai.atlas.export import ExportBuilder

        factory, cipher = self._shared()
        builder = ExportBuilder(factory, cipher, S3ObjectStorage(self._settings.s3))
        self._loop().run(builder.handle(job.export_id))
        return False

    def _image_embedder(self) -> SiglipEmbedder:
        """One SigLIP per process (loaded on first use), shared by indexing and recognition."""
        if self._embedder is None:
            from spoldzielnia_ai.atlas.embedding import SiglipEmbedder

            self._embedder = SiglipEmbedder(Path(self._settings.models.image_embed_dir))
        return self._embedder

    def _atlas_indexer(self) -> AtlasIndexer:
        """worker-core: SigLIP from the models volume, storage, Qdrant."""
        if self._atlas_index is None:
            factory, cipher = self._shared()
            settings = self._settings
            self._atlas_index = AtlasIndexer(
                factory,
                cipher,
                S3ObjectStorage(settings.s3),
                self._image_embedder(),
                AtlasVectorRepo(self._qdrant()),
            )
        return self._atlas_index

    def _build_recognizer(self) -> AtlasRecognizer:
        """worker-core: SigLIP, Qdrant, storage, the local VLM and the injection guard (9.9)."""
        factory, cipher = self._shared()
        settings = self._settings
        guard, _ = self._injection_guard()
        return AtlasRecognizer(
            factory,
            cipher,
            S3ObjectStorage(settings.s3),
            self._image_embedder(),
            AtlasVectorRepo(self._qdrant()),
            self._gateway(),
            guard,
            settings.atlas,
            max_pixels=settings.uploads.max_image_pixels,
            tmp_dir=settings.uploads.tmp_dir,
        )

    def recognize(self, job: DerivativeJob, attempt: int) -> bool:
        runner = self._loop()
        if self._recognizer is None:
            self._recognizer = runner.run(_construct(self._build_recognizer))
        runner.run(self._llm_store().current())
        return runner.run(self._recognizer.handle(job, final_attempt=attempt >= MAX_RETRIES))

    def index_atlas(self, job: AtlasIndexJob, _attempt: int) -> bool:
        indexer = self._loop().run(_construct(self._atlas_indexer))
        self._loop().run(indexer.index_image(job.image_id))
        return False

    def _index(self) -> Indexer:
        if self._indexer is None:
            self._indexer = self._loop().run(_construct(self._build_indexer))
        return self._indexer

    def index_derivative(self, job: DerivativeJob, attempt: int) -> bool:
        final = attempt >= MAX_RETRIES
        return self._loop().run(self._index().index_private_derivative(job, final_attempt=final))

    def index_article(self, job: ArticleJob, attempt: int) -> bool:
        final = attempt >= MAX_RETRIES
        return self._loop().run(self._index().index_canonical_article(job, final_attempt=final))

    def _vector_sync(self) -> VectorSync:
        if self._sync is None:
            self._sync = self._loop().run(_construct(self._build_sync))
        return self._sync

    def reconcile(self, _job: MaintenanceJob, _attempt: int) -> bool:
        self._loop().run(self._vector_sync().reconcile())
        return False  # the next beat tick is the retry

    def resume_waiting(self, _job: MaintenanceJob, _attempt: int) -> bool:
        from spoldzielnia_ai.workers.celery_adapter import send
        from spoldzielnia_ai.workers.waiting import ModelWaitResumer

        factory, _ = self._shared()

        async def dispatch(name: TaskName, job: Any) -> None:
            await asyncio.to_thread(send, self._celery, name, job)

        async def run() -> None:
            # Index generations first (10.3): parked indexing needs one to write to.
            reconciled = await self._generation_manager().reconcile()
            if reconciled.building:
                await dispatch(TaskName.REEMBED, MaintenanceJob())
            await ModelWaitResumer(factory, self._gateway(), dispatch).run()

        self._loop().run(run())
        return False  # the next beat tick is the retry

    def _generation_manager(self) -> GenerationManager:
        factory, cipher = self._shared()
        settings = self._settings
        return GenerationManager(
            factory, cipher, self._qdrant(), self._gateway(), settings.llm, settings.qdrant
        )

    def reembed(self, _job: MaintenanceJob, _attempt: int) -> bool:
        """Fill the BUILDING index generation for up to 240 s, then hand over (10.3)."""
        from spoldzielnia_ai.workers.celery_adapter import send

        factory, _ = self._shared()

        async def clusters_changed() -> None:  # the semantic detector's vector space
            await asyncio.to_thread(send, self._celery, TaskName.REFRESH_CLUSTERS, MaintenanceJob())

        reembed = ReEmbedder(
            factory,
            self._generations(),
            self._index(),
            self._generation_manager(),
            self._settings.llm,
            on_swap=clusters_changed,
        )
        run = self._loop().run(reembed.run())
        if run.more:
            send(self._celery, TaskName.REEMBED, MaintenanceJob())
        return False  # waiting or busy: the next reconcile (every minute) sends it again

    def consistency_audit(self, _job: MaintenanceJob, _attempt: int) -> bool:
        from spoldzielnia_ai.atlas.consistency import AtlasConsistency

        sync = self._vector_sync()
        factory, cipher = self._shared()
        auditor = ConsistencyAuditor(factory, self._qdrant(), sync, self._generations())
        self._loop().run(auditor.run())
        queue = CeleryProcessingQueue(factory, cipher, self._celery)
        atlas = AtlasConsistency(factory, self._qdrant(), sync, queue.atlas_canonical)
        self._loop().run(atlas.run())
        return False

    def refresh_clusters(self, _job: MaintenanceJob, _attempt: int) -> bool:
        """Topic clusters of the canonical index for the semantic detector (5.3)."""
        from redis.asyncio import Redis

        from spoldzielnia_ai.shield.clusters import refresh_clusters

        async def run() -> None:
            redis: Redis = Redis.from_url(self._settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
            try:
                generation = await self._generations().serving()
                if generation is None:
                    return
                repo = CanonicalVectorRepo(self._qdrant(), generation.canonical_collection)
                await refresh_clusters(repo, redis, self._settings.shield.semantic_clusters)
            finally:
                await redis.aclose()

        self._loop().run(run())
        return False

    def handler(self, name: TaskName) -> Callable[[Any, int], bool]:
        handlers: dict[TaskName, Callable[[Any, int], bool]] = {
            TaskName.PROMOTE_NOTES: self.promote,
            TaskName.PREPARE_ATLAS: self.prepare_atlas,
            TaskName.RECOGNIZE_ATLAS: self.recognize,
            TaskName.EXPORT_ATLAS: self.export_atlas,
            TaskName.SCAN_DERIVATIVE: self.scan,
            TaskName.INDEX_DERIVATIVE: self.index_derivative,
            TaskName.INDEX_ARTICLE: self.index_article,
            TaskName.INDEX_ATLAS: self.index_atlas,
            TaskName.RECONCILE_OUTBOX: self.reconcile,
            TaskName.CONSISTENCY_AUDIT: self.consistency_audit,
            TaskName.REFRESH_CLUSTERS: self.refresh_clusters,
            TaskName.RESUME_WAITING: self.resume_waiting,
            TaskName.REEMBED: self.reembed,
        }
        return handlers.get(name, self.run)


async def _construct[T](build: Callable[[], T]) -> T:
    """Build inside the runner's loop, so the DB engine binds to the loop that uses it."""
    return build()


def _soft_time_limit() -> type[BaseException]:
    from celery.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
        SoftTimeLimitExceeded,
    )

    exc: type[BaseException] = SoftTimeLimitExceeded  # pyright: ignore[reportUnknownVariableType]
    return exc


def build_worker(settings: WorkerSettings) -> Any:
    celery = make_celery(settings.redis.broker_url())
    runtime = WorkerRuntime(settings, celery)
    for name in TaskName:
        register(celery, name, runtime.handler(name), max_retries=MAX_RETRIES)
    return celery
