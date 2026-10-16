"""Chunk 2.2: allow-list validation by content and the archive guard (PLAN Etap 2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from spoldzielnia_ai.files.archive_guard import ArchiveLimits
from spoldzielnia_ai.files.validation import DOCX, FileRejected, validate_file
from tests.fixtures import files

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def write(tmp_path: Path) -> Callable[[bytes], Path]:
    def _write(data: bytes) -> Path:
        path = tmp_path / "upload.bin"  # spooled name never carries the user's filename
        path.write_bytes(data)
        return path

    return _write


def _rejected(path: Path, filename: str, content_type: str | None = None) -> FileRejected:
    with pytest.raises(FileRejected) as excinfo:
        validate_file(path, filename, content_type)
    return excinfo.value


# --- Accepted -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "filename", "content_type", "mime"),
    [
        (files.pdf(), "raport.pdf", "application/pdf", "application/pdf"),
        (files.png(), "ul.png", "image/png", "image/png"),
        (files.jpeg(), "ul.JPG", "image/jpeg", "image/jpeg"),
        (files.mp3(), "nagranie.mp3", "audio/mpeg", "audio/mpeg"),
        (files.wav(), "nagranie.wav", "audio/x-wav", "audio/wav"),
        (files.docx(), "plan.docx", None, DOCX),
        ("Zażółć gęślą jaźń\n".encode(), "notatka.txt", "text/plain", "text/plain"),
        (b"# Tytul\n", "notatka.md", "application/octet-stream", "text/markdown"),
        (files.plain_zip(), "paczka.zip", "application/zip", "application/zip"),
    ],
    ids=["pdf", "png", "jpeg", "mp3", "wav", "docx", "txt", "md", "zip"],
)
def test_valid_files_are_accepted(
    write: Callable[[bytes], Path], data: bytes, filename: str, content_type: str | None, mime: str
) -> None:
    assert validate_file(write(data), filename, content_type).mime == mime


# --- Executables (PLAN: exe renamed to .pdf must be 415) ----------------------------------


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (files.pe_executable(), "invoice.pdf"),
        (files.elf_executable(), "photo.jpg"),
        (files.macho_executable(), "notes.txt"),
        (files.shell_script(), "readme.txt"),
        (files.pe_executable(), "setup.exe"),
    ],
    ids=["exe-as-pdf", "elf-as-jpg", "macho-as-txt", "script-as-txt", "plain-exe"],
)
def test_executables_are_rejected(
    write: Callable[[bytes], Path], data: bytes, filename: str
) -> None:
    error = _rejected(write(data), filename, "application/pdf")
    assert (error.code, error.status) == ("executable", 415)


# --- Type confusion -----------------------------------------------------------------------


def test_polyglot_valid_pdf_with_exe_extension_is_rejected(write: Callable[[bytes], Path]) -> None:
    error = _rejected(write(files.pdf()), "update.exe", "application/pdf")
    assert (error.code, error.status) == ("extension_mismatch", 415)


@pytest.mark.parametrize(
    ("data", "filename", "content_type", "code"),
    [
        (files.png(), "ul.jpg", "image/jpeg", "extension_mismatch"),
        (files.png(), "ul.png", "application/pdf", "content_type_mismatch"),
        (files.pdf(), "raport", "application/pdf", "extension_mismatch"),
        (
            b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            "a.svg",
            "image/svg+xml",
            "type_not_allowed",
        ),
        (b"<html><script>alert(1)</script></html>", "a.txt", "text/plain", "type_not_allowed"),
        (b"\x00\x01\x02\x03" * 64, "data.txt", "text/plain", "type_not_allowed"),
        ("Zażółć".encode("cp1250"), "stare.txt", "text/plain", "text_not_utf8"),
    ],
    ids=["png-as-jpg", "declared-pdf", "no-extension", "svg", "html", "binary", "cp1250"],
)
def test_type_confusion_is_rejected(
    write: Callable[[bytes], Path], data: bytes, filename: str, content_type: str, code: str
) -> None:
    assert _rejected(write(data), filename, content_type).code == code


def test_category_size_limit(write: Callable[[bytes], Path]) -> None:
    big_text = b"a" * (5 * 1024 * 1024 + 1)
    error = _rejected(write(big_text), "duzy.txt", "text/plain")
    assert (error.code, error.status) == ("too_large", 413)


# --- Office macros ------------------------------------------------------------------------


def test_docx_with_macros_is_rejected(write: Callable[[bytes], Path]) -> None:
    error = _rejected(write(files.docx(macros=True)), "plan.docx", None)
    assert error.code == "macros_not_allowed"


# --- Archive guard (status 422) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (files.high_ratio_zip, "archive_ratio"),
        (files.nested_zip, "archive_nested"),
        (files.traversal_zip, "archive_path_traversal"),
        (files.symlink_zip, "archive_symlink"),
        (files.executable_in_zip, "archive_contains_executable"),
    ],
    ids=["zip-bomb-ratio", "zip-bomb-nested", "traversal", "symlink", "exe-inside"],
)
def test_hostile_archives_are_rejected(
    write: Callable[[bytes], Path], factory: Callable[[], bytes], code: str
) -> None:
    error = _rejected(write(factory()), "paczka.zip", "application/zip")
    assert (error.code, error.status) == (code, 422)


def test_lying_metadata_is_caught_while_decompressing(write: Callable[[bytes], Path]) -> None:
    """The directory claims 10 bytes. Decompression produces 4 MB and the counter stops it."""
    error = _rejected(write(files.lying_size_zip()), "paczka.zip", "application/zip")
    assert error.status == 422
    assert error.code in {"archive_size_mismatch", "archive_corrupt"}


def test_archive_limits_are_configurable(write: Callable[[bytes], Path]) -> None:
    many = files.plain_zip(**{f"f{i}.txt": b"x" for i in range(11)})
    with pytest.raises(FileRejected, match="archive_too_many_entries"):
        validate_file(write(many), "paczka.zip", None, ArchiveLimits(max_entries=10))
    with pytest.raises(FileRejected, match="archive_too_large"):
        validate_file(
            write(files.plain_zip()), "paczka.zip", None, ArchiveLimits(max_total_bytes=1)
        )
