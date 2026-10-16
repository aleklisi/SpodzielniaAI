"""Streaming ``multipart/form-data`` reader for uploads.

Starlette's ``request.form()`` buffers the whole file before the handler runs, with no cap on
file parts. Here the body is parsed as it arrives. File bytes are counted into a spool file and
the request is cut off at ``max_bytes`` (``Content-Length`` is never trusted). Exactly one file
part (``file``) is accepted. Other fields must be known and small.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import IO, TYPE_CHECKING

from python_multipart.multipart import MultipartParser, parse_options_header

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MAX_FIELD_BYTES = 64
MAX_FILENAME_CHARS = 255


class UploadError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class SpooledUpload:
    """The file part on disk under a random name. Call ``cleanup`` when done."""

    path: Path
    filename: str
    content_type: str | None
    size: int
    sha256: str
    fields: dict[str, str] = field(default_factory=dict[str, str])

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


def safe_filename(raw: str) -> str:
    """The base name only (no directories), printable, bounded. It is stored encrypted and
    shown back to its owner, never used as a path."""
    name = PurePosixPath(raw.replace("\\", "/")).name
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if name in {"", ".", ".."}:
        return "upload"
    return name[:MAX_FILENAME_CHARS]


class _Collector:
    """python-multipart callbacks writing the file part to ``sink``."""

    def __init__(self, sink: IO[bytes], max_bytes: int, allowed_fields: frozenset[str]) -> None:
        self.sink = sink
        self.max_bytes = max_bytes
        self.allowed_fields = allowed_fields
        self.digest = hashlib.sha256()
        self.size = 0
        self.filename: str | None = None
        self.content_type: str | None = None
        self.fields: dict[str, str] = {}
        self._headers: dict[bytes, bytes] = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._in_file = False
        self._field_name: str | None = None
        self._field_value = bytearray()

    def on_part_begin(self) -> None:
        self._headers.clear()
        self._in_file = False
        self._field_name = None
        self._field_value.clear()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        self._headers[bytes(self._header_field).lower()] = bytes(self._header_value)
        self._header_field.clear()
        self._header_value.clear()

    def on_headers_finished(self) -> None:
        _, disposition = parse_options_header(self._headers.get(b"content-disposition", b""))
        name = disposition.get(b"name", b"").decode("utf-8", errors="replace")
        if b"filename" in disposition:
            if name != "file" or self.filename is not None:
                raise UploadError("unexpected_file_part", 400)
            self.filename = safe_filename(
                disposition[b"filename"].decode("utf-8", errors="replace")
            )
            raw_type = self._headers.get(b"content-type")
            self.content_type = raw_type.decode("latin-1") if raw_type else None
            self._in_file = True
        elif name in self.allowed_fields:
            self._field_name = name
        else:
            raise UploadError("unexpected_field", 400)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._in_file:
            self.size += len(chunk)
            if self.size > self.max_bytes:
                raise UploadError("too_large", 413)
            self.digest.update(chunk)
            self.sink.write(chunk)
        else:
            self._field_value.extend(chunk)
            if len(self._field_value) > MAX_FIELD_BYTES:
                raise UploadError("field_too_large", 400)

    def on_part_end(self) -> None:
        if self._field_name is not None:
            self.fields[self._field_name] = self._field_value.decode("utf-8", errors="replace")


def _discard(sink: IO[bytes], path: Path) -> None:
    """Close and delete the spool file. It lives on tmpfs, so this does not block."""
    sink.close()
    path.unlink(missing_ok=True)


async def read_upload(
    content_type: str | None,
    body: AsyncIterator[bytes],
    *,
    max_bytes: int,
    allowed_fields: frozenset[str],
    spool_dir: str | None,
) -> SpooledUpload:
    if not content_type:
        raise UploadError("multipart_required", 400)
    kind, params = parse_options_header(content_type)
    boundary = params.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise UploadError("multipart_required", 400)

    sink = tempfile.NamedTemporaryFile(prefix="up-", dir=spool_dir, delete=False)  # noqa: SIM115
    path = Path(sink.name)
    collector = _Collector(sink, max_bytes, allowed_fields)
    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": collector.on_part_begin,
            "on_part_data": collector.on_part_data,
            "on_part_end": collector.on_part_end,
            "on_header_field": collector.on_header_field,
            "on_header_value": collector.on_header_value,
            "on_header_end": collector.on_header_end,
            "on_headers_finished": collector.on_headers_finished,
        },
    )
    try:
        async for chunk in body:
            parser.write(chunk)
        parser.finalize()
    except UploadError:
        _discard(sink, path)
        raise
    except Exception:
        _discard(sink, path)
        raise UploadError("malformed_multipart", 400) from None
    sink.close()

    if collector.filename is None:
        _discard(sink, path)
        raise UploadError("file_missing", 400)
    return SpooledUpload(
        path=path,
        filename=collector.filename,
        content_type=collector.content_type,
        size=collector.size,
        sha256=collector.digest.hexdigest(),
        fields=collector.fields,
    )
