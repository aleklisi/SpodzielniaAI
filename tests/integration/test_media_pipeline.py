"""Chunk 2.5: upload/link → queue → task → derivative, in Celery eager mode with fake engines.

Real Postgres (RLS), the real ``CeleryProcessingQueue`` and ``DerivativeProcessor``; engines and
the fetcher's network are fakes. The eager task runs in a worker thread with its own event loop
and DB engine, as a worker process would.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import IO, TYPE_CHECKING, Any

import httpx
import pytest
import respx
from PIL import Image
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
from spoldzielnia_ai.workers.engines.docling_engine import DocxEngine
from spoldzielnia_ai.workers.engines.documents import DocumentEngine
from spoldzielnia_ai.workers.engines.text import TextEngine
from spoldzielnia_ai.workers.engines.transcription import GatewayTranscriptionEngine
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
    transcription: Engine | None = None  # 10.4: the real API engine, when a test sets it
    documents: Engine | None = None  # 10.5: the real document engine, when a test sets it

    def __call__(self, kind: DerivativeKind, mime: str) -> Engine:
        if kind == DerivativeKind.TRANSCRIPT:
            return self.transcription or self.whisper
        if kind == DerivativeKind.IMAGE_DESCRIPTION:
            return UnavailableVisionEngine()
        if mime in ("text/plain", "text/markdown"):
            return TextEngine()
        return self.documents or self.docling


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


async def test_a_recording_waits_for_a_transcription_model_then_goes_to_its_api(
    h: Harness, engines: Engines, runtime: EagerRuntime, factory: SessionFactory
) -> None:
    """10.4: no TRANSCRIBE model and no built-in Whisper → WAITING_FOR_MODEL; once a cleared
    model is configured the recording is sent to its API (decrypted WAV, multipart) only."""
    current = {"llm": LlmSettings()}
    engines.transcription = GatewayTranscriptionEngine(
        lambda: LiteLLMGateway(current["llm"]), LlmSettings(), lambda: None
    )
    alice = await h.login()
    note_id = await _note(alice)
    with respx.mock(assert_all_called=False) as router:
        h.idp.mount(router)
        api = router.post("http://llm:11434/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "Matki pszczele wychodzą z ula."})
        )
        await _upload(alice, note_id, files.wav(seconds=1.0), "nagranie.wav", "audio/wav")
        (derivative,) = await _derivatives(alice, note_id)
        assert (derivative["status"], derivative["error"]) == (
            "WAITING_FOR_MODEL",
            "model_not_configured:TRANSCRIBE",
        )
        async with factory() as session:
            waiting = (
                await session.execute(
                    text("SELECT task FROM model_wait WHERE purpose = 'TRANSCRIBE'")
                )
            ).all()
        assert [tuple(r) for r in waiting] == [("media.transcribe_audio",)]
        assert api.call_count == 0

        sent: list[tuple[TaskName, Any]] = []

        async def record(name: TaskName, job: Any) -> None:
            sent.append((name, job))

        current["llm"] = LlmSettings(transcribe_model="whisper-large-v3")
        assert await ModelWaitResumer(factory, LiteLLMGateway(current["llm"]), record).run() == 1
        ((name, job),) = sent
        assert name == TaskName.TRANSCRIBE_AUDIO
        await asyncio.to_thread(runtime.run, job, 1)  # what the media worker does next

        (derivative,) = await _derivatives(alice, note_id)
        assert (derivative["status"], derivative["content"]) == (
            "DONE",
            "Matki pszczele wychodzą z ula.",
        )
        assert api.call_count == 1
        body = api.calls.last.request.content
        assert b"RIFF" in body
        assert b'name="model"\r\n\r\nwhisper-large-v3' in body


@dataclass
class VisionTransport:
    """The OCR model's endpoint: records what it was sent."""

    seen: list[list[Any]] = field(default_factory=list[list[Any]])

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        self.seen.append(messages)
        message = SimpleNamespace(content="Odczytany protokół z zebrania.")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        raise AssertionError("no embeddings here")


def _scanned_pdf() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 560), "white").save(out, "PDF")  # an image page, no text layer
    return out.getvalue()


def _text_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    pdf = canvas.Canvas(out)
    pdf.drawString(72, 720, "Protokol z przegladu pasieki numer 4, rodziny silne.")
    pdf.save()
    return out.getvalue()


async def test_a_scan_waits_for_an_ocr_model_while_text_documents_complete(
    h: Harness, engines: Engines, runtime: EagerRuntime, factory: SessionFactory
) -> None:
    """10.5: no OCR model and no built-in Docling OCR → the scan waits; a born-digital PDF
    completes with no model. Once a cleared vision model serves OCR, the scan is read by it."""
    current = {"llm": LlmSettings()}
    transport = VisionTransport()
    engines.documents = DocumentEngine(
        lambda: LiteLLMGateway(current["llm"], transport), LlmSettings(), DocxEngine()
    )
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, _text_pdf(), "protokol.pdf", "application/pdf")
    await _upload(alice, note_id, _scanned_pdf(), "skan.pdf", "application/pdf")
    done, waiting = sorted(await _derivatives(alice, note_id), key=lambda d: d["status"])
    assert done["status"] == "DONE"
    assert "przegladu pasieki numer 4" in done["content"]
    assert (waiting["status"], waiting["error"]) == (
        "WAITING_FOR_MODEL",
        "model_not_configured:OCR",
    )
    assert transport.seen == []

    sent: list[tuple[TaskName, Any]] = []

    async def record(name: TaskName, job: Any) -> None:
        sent.append((name, job))

    current["llm"] = LlmSettings(vlm_model="qwen2.5-vl")  # .env: OCR uses the vision model
    assert await ModelWaitResumer(factory, LiteLLMGateway(current["llm"]), record).run() == 1
    ((name, job),) = sent
    assert name == TaskName.PARSE_DOCUMENT
    await asyncio.to_thread(runtime.run, job, 1)

    scan = next(d for d in await _derivatives(alice, note_id) if d["id"] == waiting["id"])
    assert (scan["status"], scan["content"]) == ("DONE", "Odczytany protokół z zebrania.")
    ((_system, user),) = transport.seen
    assert user["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


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
