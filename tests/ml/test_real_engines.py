"""Chunk 2.5 (``ml``): the real engines on real inputs, offline.

Needs the host model directory (``make models-host``: whisper ``tiny`` + Docling). Skipped with
a reason otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from spoldzielnia_ai.core.settings import ModelSettings
from spoldzielnia_ai.workers import models
from spoldzielnia_ai.workers.engines.base import PermanentError
from tests.fixtures import files

pytestmark = pytest.mark.ml

REPO = Path(__file__).resolve().parents[2]
MODELS = Path(os.environ.get("SPOLDZIELNIA_TEST_MODELS", REPO / ".models"))
SPEECH = REPO / "tests" / "fixtures" / "audio" / "pl_pszczoly.wav"


def _settings() -> ModelSettings:
    if not (MODELS / "whisper" / "tiny" / "model.bin").is_file():
        pytest.skip(f"no models in {MODELS}: run `make models-host`")
    return ModelSettings(root=str(MODELS), whisper_model="tiny", whisper_beam_size=1)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Like the worker: no hub access at all."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


def test_model_volume_matches_manifest() -> None:
    _settings()
    assert models.verify(MODELS) == []


def test_whisper_transcribes_polish_speech() -> None:
    from spoldzielnia_ai.workers.engines.whisper import WhisperEngine

    text = WhisperEngine(_settings()).extract(SPEECH, "audio/wav").lower()
    # "Pszczoły zbierają nektar z kwiatów rzepaku." The tiny model garbles the start, but
    # reliably gets the end (the stack runs `small`).
    assert "kwiatów rzepaku" in text, text


def test_whisper_reports_silence(tmp_path: Path) -> None:
    from spoldzielnia_ai.workers.engines.whisper import WhisperEngine

    silent = tmp_path / "silence.wav"
    silent.write_bytes(files.wav(seconds=2.0))
    with pytest.raises(PermanentError, match="no_speech_detected"):
        WhisperEngine(_settings()).extract(silent, "audio/wav")


def test_docling_parses_pdf(tmp_path: Path) -> None:
    from spoldzielnia_ai.workers.engines.docling_engine import DoclingEngine

    pdf = tmp_path / "raport.pdf"
    pdf.write_bytes(_text_pdf("Sprawozdanie z przegladu pasieki"))
    markdown = DoclingEngine(_settings().docling_dir, ocr=True).extract(pdf, "application/pdf")
    assert "przegladu pasieki" in markdown


def test_docling_rejects_garbage(tmp_path: Path) -> None:
    from spoldzielnia_ai.workers.engines.docling_engine import DoclingEngine

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(PermanentError):
        DoclingEngine(_settings().docling_dir, ocr=True).extract(bad, "application/pdf")


def _text_pdf(line: str) -> bytes:
    """A one-page PDF with real text (Helvetica), built with pikepdf."""
    import io

    import pikepdf

    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    content = pdf.make_stream(f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET".encode())  # pyright: ignore[reportUnknownMemberType]
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 612, 792],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
        Contents=content,
    )
    pdf.pages.append(pikepdf.Page(page))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()
