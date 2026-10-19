"""Per-process worker runtime: one event loop, DB engine, clients and engines per process.

Celery's prefork pool forks children after the app is built. Everything with sockets or
threads is therefore created lazily, inside the child, on the first task.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.auth.deps import DbAuditSink
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.files.antivirus import ClamdScanner
from spoldzielnia_ai.files.storage import S3ObjectStorage
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.llm.injection_guard import InjectionGuard
from spoldzielnia_ai.net.safe_fetch import SafeFetcher
from spoldzielnia_ai.notebook.queue import CeleryProcessingQueue
from spoldzielnia_ai.notebook.uploads import UploadPipeline
from spoldzielnia_ai.privacy.scrubber import ChecksumAnalyzer, CombinedAnalyzer, PiiScrubber
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.consistency import ConsistencyAuditor
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync
from spoldzielnia_ai.wiki.promotion import PromotionProcessor
from spoldzielnia_ai.workers.celery_adapter import make_celery, register
from spoldzielnia_ai.workers.engines.text import TextEngine
from spoldzielnia_ai.workers.engines.vision import GatewayVisionEngine, UnavailableVisionEngine
from spoldzielnia_ai.workers.jobs import TaskName
from spoldzielnia_ai.workers.processing import DerivativeProcessor
from spoldzielnia_ai.workers.scan import DerivativeScanner

if TYPE_CHECKING:
    from collections.abc import Callable

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.injection_guard import Classifier
    from spoldzielnia_ai.workers.engines.base import Engine
    from spoldzielnia_ai.workers.jobs import (
        ArticleJob,
        DerivativeJob,
        MaintenanceJob,
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
        self._indexer: Indexer | None = None
        self._sync: VectorSync | None = None
        self._qdrant_client: AsyncQdrantClient | None = None
        self._guard: tuple[InjectionGuard, bool] | None = None
        self._factory: SessionFactory | None = None
        self._cipher: EnvelopeCipher | None = None
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
                self._engine_cache[key] = (
                    GatewayVisionEngine(lambda: LiteLLMGateway(self._settings.llm))
                    if self._settings.llm.vlm_model
                    else UnavailableVisionEngine()
                )
        return self._engine_cache[key]

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
        gateway = LiteLLMGateway(self._settings.llm)
        return PromotionProcessor(factory, cipher, scrubber, guard, gateway)

    def _qdrant(self) -> AsyncQdrantClient:
        if self._qdrant_client is None:
            self._qdrant_client = make_qdrant(self._settings.qdrant)
        return self._qdrant_client

    def _build_indexer(self) -> Indexer:
        """worker-core: local embeddings and Qdrant over internal TLS (4.1)."""
        factory, cipher = self._shared()
        settings = self._settings
        embedder = Embedder(LiteLLMGateway(settings.llm), settings.qdrant.vector_size)
        return Indexer(factory, cipher, embedder, self._qdrant())

    def _build_sync(self) -> VectorSync:
        """worker-core: the outbox reconciler (4.4). UPSERT ops go back to the index queue."""
        factory, cipher = self._shared()
        settings = self._settings
        queue = CeleryProcessingQueue(factory, cipher, self._celery)
        return VectorSync(
            factory,
            CanonicalVectorRepo(self._qdrant()),
            queue.article_canonical,
            settings.qdrant.timeout_seconds,
            settings.governance.outbox_alert_after,
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

    def consistency_audit(self, _job: MaintenanceJob, _attempt: int) -> bool:
        sync = self._vector_sync()
        factory, _ = self._shared()
        self._loop().run(ConsistencyAuditor(factory, self._qdrant(), sync).run())
        return False

    def refresh_clusters(self, _job: MaintenanceJob, _attempt: int) -> bool:
        """Topic clusters of the canonical index for the semantic detector (5.3)."""
        from redis.asyncio import Redis

        from spoldzielnia_ai.shield.clusters import refresh_clusters

        async def run() -> None:
            redis: Redis = Redis.from_url(self._settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
            try:
                repo = CanonicalVectorRepo(self._qdrant())
                await refresh_clusters(repo, redis, self._settings.shield.semantic_clusters)
            finally:
                await redis.aclose()

        self._loop().run(run())
        return False

    def handler(self, name: TaskName) -> Callable[[Any, int], bool]:
        handlers: dict[TaskName, Callable[[Any, int], bool]] = {
            TaskName.PROMOTE_NOTES: self.promote,
            TaskName.SCAN_DERIVATIVE: self.scan,
            TaskName.INDEX_DERIVATIVE: self.index_derivative,
            TaskName.INDEX_ARTICLE: self.index_article,
            TaskName.RECONCILE_OUTBOX: self.reconcile,
            TaskName.CONSISTENCY_AUDIT: self.consistency_audit,
            TaskName.REFRESH_CLUSTERS: self.refresh_clusters,
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
