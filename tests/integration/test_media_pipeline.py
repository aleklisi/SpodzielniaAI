"""Chunk 2.5: upload/link → queue → task → derivative, in Celery eager mode with fake engines.

Real Postgres (RLS), the real ``CeleryProcessingQueue`` and ``DerivativeProcessor``; engines and
the fetcher's network are fakes. The eager task runs in a worker thread with its own event loop
and DB engine, as a worker process would.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.auth.deps import DbAuditSink
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.files.antivirus import ScanResult
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.net.safe_fetch import SafeFetcher
from spoldzielnia_ai.notebook.queue import CeleryProcessingQueue
from spoldzielnia_ai.notebook.uploads import UploadPipeline
from spoldzielnia_ai.workers.celery_adapter import make_celery, register
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError
from spoldzielnia_ai.workers.engines.text import TextEngine
from spoldzielnia_ai.workers.engines.vision import UnavailableVisionEngine
from spoldzielnia_ai.workers.jobs import DerivativeJob, TaskName
from spoldzielnia_ai.workers.processing import DerivativeProcessor
from spoldzielnia_ai.workers.waiting import ModelWaitResumer
from tests.fixtures import files
from tests.fixtures.app import Harness, LoggedIn, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.workers.engines.base import Engine
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

PUBLIC = ipaddress.ip_address("93.184.216.34")


class CleanScanner:
    async def scan(self, src: IO[bytes]) -> ScanResult:
        return ScanResult(clean=True)


@dataclass
class FakeEngine:
    result: str = "Pszczoły zbierają nektar."
    error: Exception | None = None
    seen: list[tuple[str, bytes]] = field(default_factory=list[tuple[str, bytes]])

    def extract(self, path: Path, mime_type: str) -> str:
        self.seen.append((path.suffix, path.read_bytes()[:8]))
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class Engines:
    whisper: FakeEngine = field(default_factory=FakeEngine)
    docling: FakeEngine = field(default_factory=lambda: FakeEngine("# Raport\n\nTreść."))

    def __call__(self, kind: DerivativeKind, mime: str) -> Engine:
        if kind == DerivativeKind.TRANSCRIPT:
            return self.whisper
        if kind == DerivativeKind.IMAGE_DESCRIPTION:
            return UnavailableVisionEngine()
        if mime in ("text/plain", "text/markdown"):
            return TextEngine()
        return self.docling


class EagerRuntime:
    """Builds the processor lazily, inside the eager task's own thread and loop."""

    def __init__(self, h: Harness, app_url: str, engines: Engines, celery: Any) -> None:
        self._h, self._url, self._engines, self._celery = h, app_url, engines, celery
        self.answers: list[Any] = [PUBLIC]  # what "DNS" says in the worker

    async def _handle(self, job: DerivativeJob, attempt: int) -> bool:
        engine = create_async_engine(self._url)
        try:
            factory = create_session_factory(engine)
            state = self._h.app.state

            class _S:
                session_factory = factory

            uploads = UploadPipeline(
                factory,
                state.cipher,
                state.storage,
                CleanScanner(),
                state.settings.uploads,
                DbAuditSink(_S()),
                CeleryProcessingQueue(factory, state.cipher, self._celery),
            )

            async def resolver(host: str, port: int) -> list[Any]:
                return self.answers

            processor = DerivativeProcessor(
                factory,
                state.cipher,
                state.storage,
                self._engines,
                fetcher=SafeFetcher(state.settings.fetch, resolver),
                uploads=uploads,
            )
            return await processor.handle(job, final_attempt=attempt >= 3)
        finally:
            await engine.dispose()

    def run(self, job: DerivativeJob, attempt: int) -> bool:
        return asyncio.run(self._handle(job, attempt))


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def engines() -> Engines:
    return Engines()


@pytest.fixture
async def stack(
    settings: Settings, factory: SessionFactory, migrated: PgInstance, engines: Engines
) -> AsyncIterator[tuple[Harness, EagerRuntime]]:
    async with harness(settings, factory) as h:
        celery = make_celery("memory://", eager=True)
        runtime = EagerRuntime(h, migrated.app_url, engines, celery)
        for name in TaskName:
            register(celery, name, runtime.run)
        h.app.state.scanner = CleanScanner()
        h.app.state.processing_queue = CeleryProcessingQueue(factory, h.app.state.cipher, celery)
        yield h, runtime


@pytest.fixture
def h(stack: tuple[Harness, EagerRuntime]) -> Harness:
    return stack[0]


@pytest.fixture
def runtime(stack: tuple[Harness, EagerRuntime]) -> EagerRuntime:
    return stack[1]


async def _note(user: LoggedIn) -> str:
    response = await user.client.post("/notes", json={"title": "Media"}, headers=user.headers)
    return response.json()["id"]


async def _upload(user: LoggedIn, note_id: str, data: bytes, name: str, ctype: str) -> Any:
    response = await user.client.post(
        f"/notes/{note_id}/attachments", files={"file": (name, data, ctype)}, headers=user.headers
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _derivatives(user: LoggedIn, note_id: str) -> list[dict[str, Any]]:
    return (await user.client.get(f"/notes/{note_id}")).json()["derivatives"]


async def test_wav_upload_produces_transcript(h: Harness, engines: Engines) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    attachment = await _upload(alice, note_id, files.wav(), "nagranie.wav", "audio/wav")

    (derivative,) = await _derivatives(alice, note_id)
    assert derivative["kind"] == "TRANSCRIPT"
    assert derivative["status"] == "DONE"
    assert derivative["content"] == "Pszczoły zbierają nektar."
    assert derivative["attachment_id"] == attachment["id"]
    # The engine got the decrypted file with a proper suffix, never ciphertext.
    ((suffix, head),) = engines.whisper.seen
    assert (suffix, head[:4]) == (".wav", b"RIFF")

    status = (await alice.client.get(f"/notes/{note_id}/attachments/{attachment['id']}")).json()
    assert [d["status"] for d in status["derivatives"]] == ["DONE"]


async def test_pdf_and_text_are_parsed(h: Harness, engines: Engines) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, files.pdf(), "raport.pdf", "application/pdf")
    await _upload(alice, note_id, "Zwykły tekst".encode(), "a.txt", "text/plain")
    contents = {d["content"] for d in await _derivatives(alice, note_id)}
    assert contents == {"# Raport\n\nTreść.", "Zwykły tekst"}
    assert engines.docling.seen[0][0] == ".pdf"


async def test_an_image_waits_for_a_vision_model_then_resumes(
    h: Harness, factory: SessionFactory
) -> None:
    """10.2: no vision model → WAITING_FOR_MODEL (not a failure); resumed once there is one."""
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, files.png(), "ul.png", "image/png")
    (derivative,) = await _derivatives(alice, note_id)
    assert (derivative["status"], derivative["error"]) == (
        "WAITING_FOR_MODEL",
        "model_not_configured:VLM",
    )
    async with factory() as session:
        failed = await session.scalar(
            text("SELECT count(*) FROM security_audit_log WHERE event_type = 'PROCESSING_FAILED'")
        )
        waiting = (await session.execute(text("SELECT purpose, task FROM model_wait"))).all()
    assert failed == 0
    assert [tuple(r) for r in waiting] == [("VLM", "media.describe_image")]

    sent: list[tuple[TaskName, Any]] = []

    async def record(name: TaskName, job: Any) -> None:
        sent.append((name, job))

    still_none = ModelWaitResumer(factory, LiteLLMGateway(LlmSettings()), record)
    assert await still_none.run() == 0
    assert sent == []

    configured = LiteLLMGateway(LlmSettings(vlm_model="qwen-vl"))
    assert await ModelWaitResumer(factory, configured, record).run() == 1
    ((name, job),) = sent
    assert name == TaskName.DESCRIBE_IMAGE
    assert str(job.derivative_id) == derivative["id"]
    (derivative,) = await _derivatives(alice, note_id)
    assert (derivative["status"], derivative["error"]) == ("PENDING", None)
    async with factory() as session:
        assert await session.scalar(text("SELECT count(*) FROM model_wait")) == 0
    assert await ModelWaitResumer(factory, configured, record).run() == 0


async def test_permanent_engine_error_fails_without_retry(h: Harness, engines: Engines) -> None:
    engines.whisper.error = PermanentError("audio_undecodable")
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, files.wav(), "a.wav", "audio/wav")
    (derivative,) = await _derivatives(alice, note_id)
    assert (derivative["status"], derivative["error"]) == ("FAILED", "audio_undecodable")
    assert len(engines.whisper.seen) == 1


async def test_transient_errors_retry_then_fail(h: Harness, engines: Engines) -> None:
    engines.whisper.error = TransientError("gpu busy")
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, files.wav(), "a.wav", "audio/wav")
    (derivative,) = await _derivatives(alice, note_id)
    assert (derivative["status"], derivative["error"]) == ("FAILED", "retries_exhausted")
    assert len(engines.whisper.seen) == 4  # first try + 3 retries


async def test_html_link_becomes_markdown(h: Harness) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    html = (
        "<html><body><article><h1>Warroza</h1><p>Kwas szczawiowy podaje się zimą, w okresie "
        "bezczerwiowym, metodą odparowania lub polewania.</p></article></body></html>"
    )
    with respx.mock(assert_all_called=False) as router:
        h.idp.mount(router)
        router.get(f"https://{PUBLIC}/warroza").mock(return_value=httpx.Response(200, html=html))
        response = await alice.client.post(
            f"/notes/{note_id}/links",
            json={"url": "https://pszczoly.example.org/warroza"},
            headers=alice.headers,
        )
    assert response.status_code == 202
    (derivative,) = await _derivatives(alice, note_id)
    assert derivative["kind"] == "FETCHED_LINK"
    assert derivative["status"] == "DONE", derivative
    assert "Kwas szczawiowy" in derivative["content"]


async def test_pdf_link_becomes_parsed_attachment(h: Harness) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    with respx.mock(assert_all_called=False) as router:
        h.idp.mount(router)
        router.get(f"https://{PUBLIC}/docs/raport.pdf").mock(
            return_value=httpx.Response(
                200, content=files.pdf(), headers={"content-type": "application/pdf"}
            )
        )
        await alice.client.post(
            f"/notes/{note_id}/links",
            json={"url": "https://pszczoly.example.org/docs/raport.pdf"},
            headers=alice.headers,
        )
    note = (await alice.client.get(f"/notes/{note_id}")).json()
    (attachment,) = note["attachments"]
    assert attachment["filename"] == "raport.pdf"
    kinds = {d["kind"]: d for d in note["derivatives"]}
    assert kinds["FETCHED_LINK"]["status"] == "DONE"
    assert kinds["FETCHED_LINK"]["attachment_id"] == attachment["id"]
    assert kinds["PARSED_DOC"]["status"] == "DONE"
    assert kinds["PARSED_DOC"]["content"] == "# Raport\n\nTreść."


async def test_link_resolving_privately_fails_at_fetch_time(
    h: Harness, runtime: EagerRuntime
) -> None:
    """The API accepted the name. DNS in the worker says 10.0.0.5 → FAILED, nothing fetched."""
    runtime.answers = [ipaddress.ip_address("10.0.0.5")]
    alice = await h.login()
    note_id = await _note(alice)
    with respx.mock(assert_all_called=False) as router:
        h.idp.mount(router)
        anything = router.get(url__regex=r".*example\.org.*|.*10\.0\.0\.5.*")
        await alice.client.post(
            f"/notes/{note_id}/links",
            json={"url": "https://rebind.example.org/"},
            headers=alice.headers,
        )
    (derivative,) = await _derivatives(alice, note_id)
    assert derivative["status"] == "FAILED"
    assert derivative["error"] == "ssrf_blocked:private_address"
    assert not anything.called
