"""Documents → Markdown with no model required; scanned pages through the OCR purpose (10.5).

- DOCX: Docling's simple pipeline (no model). ODT: ``odt_to_markdown`` (no model).
- PDF: pdfium reads each page's text layer. A page with fewer than ``MIN_PAGE_CHARS`` characters
  is a scan. Born-digital documents (no scans) are converted by Docling's layout pipeline when its
  models are installed (headings, tables), else from the text layer.
- Scanned pages, decided per job and never as a fallback after a failure (P5):
  1. OCR has a usable model (a vision model of a provider cleared for private data): each scanned
     page is rendered to a JPEG on the worker's tmpfs and sent inline to that model only, with
     ``OCR_PAGE``; text pages keep their text layer.
  2. Otherwise Docling's own OCR, if ``llm.builtin_ocr`` and its models are installed.
  3. Otherwise the derivative waits for a model (WAITING_FOR_MODEL); born-digital documents
     complete meanwhile.

The OCR output is a derivative like any other: worker-core screens it for prompt injection.
"""

from __future__ import annotations

import asyncio
import base64
import io
from typing import TYPE_CHECKING, Any, Protocol

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.llm.gateway import (
    LLMError,
    LLMPurpose,
    ModelNotConfigured,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.llm.prompts import OCR_PAGE
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError, WaitingForModel
from spoldzielnia_ai.workers.engines.odt import odt_to_markdown

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMResult
    from spoldzielnia_ai.workers.engines.base import Engine

log = get_logger(__name__)

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT = "application/vnd.oasis.opendocument.text"
MIN_PAGE_CHARS = 20
RENDER_SCALE = 2.0  # 144 dpi
MAX_SIDE = 2000


class OcrGateway(Protocol):
    """What the engine needs from ``LiteLLMGateway``."""

    async def refresh(self) -> Any: ...

    def configured(self, purpose: LLMPurpose, private_data: bool = False) -> bool: ...

    async def complete(
        self, messages: list[ChatMessage], *, purpose: LLMPurpose, private_data: bool = False
    ) -> LLMResult[Any]: ...


def pdf_page_texts(path: Path) -> list[str]:
    """The text layer of every page (empty for scans)."""
    import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

    try:
        document: Any = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pdfium refuses broken or encrypted files
        raise PermanentError("document_unparseable") from exc
    try:
        texts: list[str] = []
        for index in range(len(document)):
            page = document[index]
            text_page = page.get_textpage()
            texts.append(str(text_page.get_text_range()).replace("\r\n", "\n").strip())
            text_page.close()
            page.close()
        return texts
    finally:
        document.close()


def is_scan(text: str) -> bool:
    return len("".join(text.split())) < MIN_PAGE_CHARS


def render_page(path: Path, index: int) -> str:
    """Page ``index`` as an inline JPEG data URL (never a link)."""
    import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]

    document: Any = pdfium.PdfDocument(str(path))
    try:
        page = document[index]
        image = page.render(scale=RENDER_SCALE).to_pil()
        page.close()
    finally:
        document.close()
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


class DocumentEngine:
    """Runs in a worker thread: OCR calls get their own event loop for the async gateway."""

    def __init__(
        self,
        gateway: Callable[[], OcrGateway],
        settings: LlmSettings,
        docx: Engine,
        docling: Callable[[bool], Engine | None] = lambda _ocr: None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._docx = docx
        self._docling = docling  # (with_ocr) -> the built-in Docling PDF engine, if installed

    def extract(self, path: Path, mime_type: str) -> str:
        if mime_type == DOCX:
            return self._docx.extract(path, mime_type)
        if mime_type == ODT:
            return odt_to_markdown(path)
        if mime_type != PDF:
            raise PermanentError("unsupported_document")
        pages = pdf_page_texts(path)
        scans = [i for i, text in enumerate(pages) if is_scan(text)]
        if not scans:
            layout = self._docling(False)
            return layout.extract(path, mime_type) if layout is not None else _join(pages)
        gateway = self._gateway()
        asyncio.run(gateway.refresh())
        if gateway.configured(LLMPurpose.OCR):
            if len(scans) > self._settings.ocr_max_pages:
                raise PermanentError("ocr_too_many_pages")
            read = asyncio.run(self.read_pages(gateway, path, scans))
            return _join([read.get(i, text) for i, text in enumerate(pages)])
        builtin = self._docling(True)
        if builtin is not None:
            return builtin.extract(path, mime_type)
        raise WaitingForModel(LLMPurpose.OCR.value)

    async def read_pages(
        self, gateway: OcrGateway, path: Path, indexes: list[int]
    ) -> dict[int, str]:
        system, _ = OCR_PAGE.messages([])
        out: dict[int, str] = {}
        for count, index in enumerate(indexes, 1):
            image = await asyncio.to_thread(render_page, path, index)
            user: ChatMessage = {
                "role": "user",
                "content": [
                    {"type": "text", "text": OCR_PAGE.task},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            }
            try:
                result = await gateway.complete(
                    [system[0], user], purpose=LLMPurpose.OCR, private_data=True
                )
            except ModelNotConfigured as exc:  # un-cleared or un-routed meanwhile
                raise WaitingForModel(LLMPurpose.OCR.value) from exc
            except ModelUnavailable as exc:
                raise TransientError(str(exc)) from exc
            except ModelRejected as exc:
                raise PermanentError("ocr_rejected") from exc
            except LLMError as exc:
                raise TransientError(str(exc)) from exc
            out[index] = result.content.strip()
            log.info("worker.ocr_page", page=count, of=len(indexes))
        return out


def _join(pages: list[str]) -> str:
    return "\n\n".join(p.strip() for p in pages if p.strip())
