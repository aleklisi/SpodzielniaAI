"""Docling, offline. DOCX needs no model; the PDF layout/table/OCR models are optional (10.5).

- ``DocxEngine``: Docling's simple pipeline, no model at all.
- ``DoclingEngine``: the PDF pipeline with the models from the read-only volume (layout,
  TableFormer and, with ``ocr``, RapidOCR). Used only when those models are installed: born-digital
  PDFs get headings and tables, and scanned pages are read on the server when no OCR model is
  routed (``workers.engines.documents``).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from spoldzielnia_ai.workers.engines.base import PermanentError


def docling_models_present(directory: str) -> bool:
    """Whether ``make models`` put Docling's layout model into the volume."""
    root = Path(directory)
    return root.is_dir() and any(root.iterdir())


def _markdown(converter: Any, path: Path) -> str:
    try:
        result = converter.convert(str(path), raises_on_error=True)
        markdown = str(result.document.export_to_markdown())
    except Exception as exc:  # docling raises many unrelated types for bad input
        raise PermanentError("document_unparseable") from exc
    if not markdown.strip():
        raise PermanentError("document_empty")
    return markdown


class DocxEngine:
    def __init__(self) -> None:
        self._converter: Any = None
        self._lock = threading.Lock()

    def extract(self, path: Path, mime_type: str) -> str:
        with self._lock:
            if self._converter is None:
                from docling.datamodel.base_models import InputFormat
                from docling.document_converter import DocumentConverter

                self._converter = DocumentConverter(allowed_formats=[InputFormat.DOCX])
        return _markdown(self._converter, path)


class DoclingEngine:
    def __init__(self, artifacts: str, ocr: bool) -> None:
        self._artifacts = artifacts
        self._ocr = ocr
        self._converter: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._converter is None:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption

                options = PdfPipelineOptions(
                    artifacts_path=self._artifacts,
                    do_ocr=self._ocr,
                    do_table_structure=True,
                    enable_remote_services=False,  # never call out, even if asked
                )
                self._converter = DocumentConverter(
                    allowed_formats=[InputFormat.PDF],
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
                )
            return self._converter

    def extract(self, path: Path, mime_type: str) -> str:
        return _markdown(self._load(), path)
