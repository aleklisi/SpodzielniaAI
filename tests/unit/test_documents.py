"""Chunk 10.5: documents without models; scanned pages through the OCR purpose (no fallback)."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from PIL import Image, ImageDraw
from prometheus_client import REGISTRY
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.llm.gateway import (
    Deployment,
    LiteLLMGateway,
    LLMPurpose,
    ResolvedLlmConfig,
    StaticConfig,
)
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError, WaitingForModel
from spoldzielnia_ai.workers.engines.documents import DOCX, ODT, PDF, DocumentEngine
from spoldzielnia_ai.workers.engines.odt import odt_to_markdown

if TYPE_CHECKING:
    from pathlib import Path

# ASCII: reportlab's base font has no Polish glyphs.
TEXT = "Sprawozdanie z przegladu pasieki: rodziny silne, zapas pokarmu wystarczajacy."


def _pdf(path: Path, pages: list[str | None]) -> Path:
    """``None`` is a scanned page: an image with no text layer."""
    pdf = canvas.Canvas(str(path))
    for page in pages:
        if page is None:
            scan = Image.new("RGB", (600, 300), "white")
            ImageDraw.Draw(scan).rectangle((40, 40, 560, 260), outline="black", width=4)
            pdf.drawImage(  # pyright: ignore[reportUnknownMemberType]
                ImageReader(scan), 50, 400, width=500, height=250
            )
        else:
            pdf.drawString(72, 720, page)
        pdf.showPage()
    pdf.save()
    return path


@dataclass
class Transport:
    reply: str = "Odczytany tekst ze skanu."
    error: Exception | None = None
    calls: list[list[Any]] = field(default_factory=list[list[Any]])

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))], usage=None
        )

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        raise AssertionError("no embeddings here")


VLM = Deployment("lm/qwen-vl", "qwen-vl", "http://llm:11434/v1", None, local=True)


def _gateway(transport: Transport, deployment: Deployment | None = VLM) -> LiteLLMGateway:
    routes = {LLMPurpose.OCR: deployment.name} if deployment else {}
    deployments = {deployment.name: deployment} if deployment else {}
    config = ResolvedLlmConfig(1, deployments, routes)
    return LiteLLMGateway(LlmSettings(), transport, source=StaticConfig(config))


class Builtin:
    def __init__(self, text: str = "z wbudowanego Doclinga") -> None:
        self.text = text
        self.calls = 0

    def extract(self, path: Path, mime_type: str) -> str:
        self.calls += 1
        return self.text


def _engine(
    transport: Transport,
    deployment: Deployment | None = VLM,
    builtin: dict[bool, Builtin] | None = None,
    **settings: Any,
) -> DocumentEngine:
    found = builtin or {}
    return DocumentEngine(
        lambda: _gateway(transport, deployment),
        LlmSettings(**settings),
        Builtin("docx"),
        lambda ocr: found.get(ocr),
    )


def test_a_born_digital_pdf_needs_no_model(tmp_path: Path) -> None:
    transport = Transport()
    pdf = _pdf(tmp_path / "a.pdf", [TEXT, "Druga strona raportu z pasieki numer trzy."])
    text = _engine(transport, deployment=None).extract(pdf, PDF)
    assert "przegladu pasieki" in text
    assert "Druga strona" in text
    assert transport.calls == []


def test_the_built_in_layout_pipeline_is_used_when_installed(tmp_path: Path) -> None:
    layout = Builtin("# Sprawozdanie")
    pdf = _pdf(tmp_path / "a.pdf", [TEXT])
    assert _engine(Transport(), builtin={False: layout}).extract(pdf, PDF) == "# Sprawozdanie"


def test_scanned_pages_go_to_the_ocr_model_and_keep_their_place(tmp_path: Path) -> None:
    transport = Transport()
    pdf = _pdf(tmp_path / "m.pdf", [TEXT, None, "Trzecia strona z tekstem dla pewnosci."])
    text = _engine(transport).extract(pdf, PDF)

    assert text.split("\n\n") == [
        TEXT,
        "Odczytany tekst ze skanu.",
        "Trzecia strona z tekstem dla pewnosci.",
    ]
    (messages,) = transport.calls  # one scanned page, one request
    image = messages[1]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")  # inline, never a link
    assert "dane do przepisania" in messages[0]["content"]


def test_without_an_ocr_model_scans_use_docling_ocr_or_wait(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "s.pdf", [None])
    ocr = Builtin()
    assert _engine(Transport(), deployment=None, builtin={True: ocr}).extract(pdf, PDF) == ocr.text
    with pytest.raises(WaitingForModel) as waiting:
        _engine(Transport(), deployment=None).extract(pdf, PDF)
    assert waiting.value.purpose == "OCR"


def test_a_routed_ocr_model_is_never_replaced_by_the_built_in_one(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "s.pdf", [None])
    ocr = Builtin()
    transport = Transport(error=type("Down", (Exception,), {"status_code": 503})())
    with pytest.raises(TransientError):
        _engine(transport, builtin={True: ocr}).extract(pdf, PDF)
    assert ocr.calls == 0
    rejected = Transport(error=type("Bad", (Exception,), {"status_code": 400})())
    with pytest.raises(PermanentError, match="ocr_rejected"):
        _engine(rejected).extract(pdf, PDF)


def test_an_uncleared_provider_never_sees_a_scan(tmp_path: Path) -> None:
    transport = Transport()
    uncleared = Deployment(
        "x/vl", "vl", "http://other:1234/v1", None, local=True, private_data=False
    )
    before = REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "OCR"}) or 0.0
    with pytest.raises(WaitingForModel):
        _engine(transport, deployment=uncleared).extract(_pdf(tmp_path / "s.pdf", [None]), PDF)
    assert transport.calls == []
    after = REGISTRY.get_sample_value("llm_route_refused_total", {"purpose": "OCR"}) or 0.0
    assert after == before  # not even attempted: ``configured`` already says no


def test_too_many_scanned_pages_fail_the_document(tmp_path: Path) -> None:
    pdf = _pdf(tmp_path / "s.pdf", [None, None, None])
    transport = Transport()
    with pytest.raises(PermanentError, match="ocr_too_many_pages"):
        _engine(transport, ocr_max_pages=2).extract(pdf, PDF)
    assert transport.calls == []


def test_broken_pdf_is_permanent(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(PermanentError, match="document_unparseable"):
        _engine(Transport()).extract(bad, PDF)


def _odt(content: str) -> bytes:
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">
<office:body><office:text>{content}</office:text></office:body></office:document-content>"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("mimetype", ODT)
        archive.writestr("content.xml", body)
    return out.getvalue()


def test_odt_becomes_markdown_without_a_model(tmp_path: Path) -> None:
    odt = tmp_path / "a.odt"
    odt.write_bytes(
        _odt(
            '<text:h text:outline-level="2">Przegląd</text:h>'
            '<text:p>Rodziny<text:s text:c="2"/>silne.<text:line-break/>Bez matecznikow.</text:p>'
            "<text:list><text:list-item><text:p>Ul 1</text:p></text:list-item>"
            "<text:list-item><text:p>Ul 2</text:p></text:list-item></text:list>"
            "<table:table><table:table-row><table:table-cell><text:p>Ul</text:p></table:table-cell>"
            "<table:table-cell><text:p>Waga</text:p></table:table-cell></table:table-row>"
            "<table:table-row><table:table-cell><text:p>1</text:p></table:table-cell>"
            "<table:table-cell><text:p>12 kg</text:p></table:table-cell></table:table-row>"
            "</table:table>"
        )
    )
    assert _engine(Transport(), deployment=None).extract(odt, ODT) == (
        "## Przegląd\n\nRodziny  silne.\nBez matecznikow.\n\n- Ul 1\n- Ul 2\n\n"
        "| Ul | Waga |\n| --- | --- |\n| 1 | 12 kg |"
    )


def test_odt_with_an_entity_bomb_or_garbage_is_refused(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.odt"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "content.xml",
            '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>',
        )
    bomb.write_bytes(out.getvalue())
    with pytest.raises(PermanentError, match="document_unparseable"):
        odt_to_markdown(bomb)
    junk = tmp_path / "junk.odt"
    junk.write_bytes(b"not a zip")
    with pytest.raises(PermanentError, match="document_unparseable"):
        odt_to_markdown(junk)


def test_docx_goes_to_the_model_free_docling_pipeline(tmp_path: Path) -> None:
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"PK")
    transport = Transport()
    assert _engine(transport, deployment=None).extract(docx, DOCX) == "docx"
    assert transport.calls == []


def test_real_docx_parses_without_models(tmp_path: Path) -> None:
    document_module = pytest.importorskip("docx")  # python-docx comes with docling
    from spoldzielnia_ai.workers.engines.docling_engine import DocxEngine

    document: Any = document_module.Document()
    document.add_heading("Przegląd pasieki", level=1)
    document.add_paragraph("Rodziny silne, zapas pokarmu wystarczający.")
    path = tmp_path / "a.docx"
    document.save(str(path))
    markdown = DocxEngine().extract(path, DOCX)
    assert "# Przegląd pasieki" in markdown
    assert "zapas pokarmu" in markdown
