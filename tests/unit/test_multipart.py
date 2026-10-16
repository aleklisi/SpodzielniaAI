"""Chunk 2.3: streaming multipart reader (size cap while streaming, strict fields)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from spoldzielnia_ai.files.multipart import UploadError, read_upload, safe_filename

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

BOUNDARY = "----spoldzielnia"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def _part(name: str, value: bytes, filename: str | None = None, ctype: str | None = None) -> bytes:
    disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
    headers = f"Content-Disposition: {disposition}\r\n"
    if ctype:
        headers += f"Content-Type: {ctype}\r\n"
    return f"--{BOUNDARY}\r\n{headers}\r\n".encode() + value + b"\r\n"


def _body(*parts: bytes) -> bytes:
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


async def _stream(data: bytes, chunk: int = 7) -> AsyncIterator[bytes]:
    for i in range(0, len(data), chunk):
        yield data[i : i + chunk]


async def _read(body: bytes, max_bytes: int = 1000, tmp: Path | None = None) -> object:
    return await read_upload(
        CONTENT_TYPE,
        _stream(body),
        max_bytes=max_bytes,
        allowed_fields=frozenset({"keep_location"}),
        spool_dir=str(tmp) if tmp else None,
    )


async def test_file_and_field_are_read(tmp_path: Path) -> None:
    data = b"%PDF-1.4 tresc"
    upload = await read_upload(
        CONTENT_TYPE,
        _stream(
            _body(_part("keep_location", b"true"), _part("file", data, "a.pdf", "application/pdf"))
        ),
        max_bytes=1000,
        allowed_fields=frozenset({"keep_location"}),
        spool_dir=str(tmp_path),
    )
    try:
        assert upload.path.read_bytes() == data
        assert upload.path.parent == tmp_path
        assert upload.path.name != "a.pdf"  # random spool name
        assert upload.filename == "a.pdf"
        assert upload.content_type == "application/pdf"
        assert upload.size == len(data)
        assert upload.sha256 == hashlib.sha256(data).hexdigest()
        assert upload.fields == {"keep_location": "true"}
    finally:
        upload.cleanup()
    assert not upload.path.exists()


async def test_size_cap_applies_while_streaming(tmp_path: Path) -> None:
    with pytest.raises(UploadError) as excinfo:
        await _read(_body(_part("file", b"x" * 1001, "a.txt")), max_bytes=1000, tmp=tmp_path)
    assert (excinfo.value.code, excinfo.value.status) == ("too_large", 413)
    assert list(tmp_path.iterdir()) == []  # spool removed


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (_body(_part("keep_location", b"true")), "file_missing"),
        (_body(_part("file", b"a", "a.txt"), _part("file", b"b", "b.txt")), "unexpected_file_part"),
        (_body(_part("other", b"a", "a.txt")), "unexpected_file_part"),
        (_body(_part("file", b"a", "a.txt"), _part("evil", b"1")), "unexpected_field"),
        (
            _body(_part("keep_location", b"x" * 100), _part("file", b"a", "a.txt")),
            "field_too_large",
        ),
        (b"not multipart at all", "file_missing"),
    ],
    ids=["no-file", "two-files", "wrong-file-field", "unknown-field", "big-field", "garbage"],
)
async def test_malformed_bodies_are_refused(tmp_path: Path, body: bytes, code: str) -> None:
    with pytest.raises(UploadError) as excinfo:
        await _read(body, tmp=tmp_path)
    assert excinfo.value.code in {code, "malformed_multipart"}
    assert excinfo.value.status == 400
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("content_type", [None, "application/json", "multipart/form-data"])
async def test_requires_multipart_with_boundary(content_type: str | None) -> None:
    with pytest.raises(UploadError, match="multipart_required"):
        await read_upload(
            content_type, _stream(b""), max_bytes=1, allowed_fields=frozenset(), spool_dir=None
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\jan\\raport.pdf", "raport.pdf"),
        ("ul\x00\n.pdf", "ul.pdf"),
        ("", "upload"),
        ("..", "upload"),
        ("a" * 300 + ".pdf", "a" * 255),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected
