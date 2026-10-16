"""PDF/DOCX/ODT → Markdown with Docling, offline (models from the read-only volume)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.workers.engines.base import PermanentError

if TYPE_CHECKING:
    from pathlib import Path

    from spoldzielnia_ai.core.settings import ModelSettings

_ODT = "application/vnd.oasis.opendocument.text"


class DoclingEngine:
    def __init__(self, settings: ModelSettings) -> None:
        self._settings = settings
        self._converter: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._converter is None:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption

                options = PdfPipelineOptions(
                    artifacts_path=self._settings.docling_dir,
                    do_ocr=self._settings.docling_ocr,
                    do_table_structure=True,
                    enable_remote_services=False,  # never call out, even if asked
                )
                self._converter = DocumentConverter(
                    allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
                )
            return self._converter

    def extract(self, path: Path, mime_type: str) -> str:
        if mime_type == _ODT:
            raise PermanentError("odt_not_supported")
        converter = self._load()
        try:
            result = converter.convert(str(path), raises_on_error=True)
            markdown = str(result.document.export_to_markdown())
        except Exception as exc:  # docling raises many unrelated types for bad input
            raise PermanentError("document_unparseable") from exc
        if not markdown.strip():
            raise PermanentError("document_empty")
        return markdown
