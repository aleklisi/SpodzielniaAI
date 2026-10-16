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
from spoldzielnia_ai.workers.celery_adapter import make_celery, register
from spoldzielnia_ai.workers.engines.text import TextEngine
from spoldzielnia_ai.workers.engines.vision import GatewayVisionEngine, UnavailableVisionEngine
from spoldzielnia_ai.workers.jobs import TaskName
from spoldzielnia_ai.workers.processing import DerivativeProcessor
from spoldzielnia_ai.workers.scan import DerivativeScanner

if TYPE_CHECKING:
    from collections.abc import Callable

    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.injection_guard import Classifier
    from spoldzielnia_ai.workers.engines.base import Engine
    from spoldzielnia_ai.workers.jobs import DerivativeJob

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
        model_dir = Path(self._settings.models.injection_dir)
        classifier: Classifier | None = None
        if (model_dir / "model.safetensors").is_file():
            from spoldzielnia_ai.llm.injection_model import InjectionClassifier

            classifier = InjectionClassifier(model_dir)
        else:
            log.error("worker.injection_classifier_missing", path=str(model_dir))
        return DerivativeScanner(
            factory, cipher, InjectionGuard(classifier), classifier is not None
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

    def handler(self, name: TaskName) -> Callable[[DerivativeJob, int], bool]:
        return self.scan if name == TaskName.SCAN_DERIVATIVE else self.run


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
