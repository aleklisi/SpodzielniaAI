"""ZIP inspection against bombs, nesting, traversal and smuggled executables.

Two passes: the central directory first (cheap), then every entry is **stream-decompressed**
with byte counters, because sizes in the directory can lie. Nothing is ever written to disk.
Archive problems are HTTP 422 (``FileRejected(status=422)``).
"""

from __future__ import annotations

import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_CHUNK = 64 * 1024
_RATIO_MIN_SIZE = 1024 * 1024  # tiny entries may compress extremely well; totals bound them

# Archive formats an entry must not be (nesting depth is 1: an archive inside the archive).
_ARCHIVE_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"\x1f\x8b",  # gzip
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\x28\xb5\x2f\xfd",  # zstd
    b"MSCF",  # cab
)


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_entries: int = 1000
    max_total_bytes: int = 500 * 1024 * 1024
    max_ratio: int = 100


def _reject(code: str) -> Exception:
    from spoldzielnia_ai.files.validation import FileRejected

    return FileRejected(code, status=422)


def _unsafe_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name:
        return True
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return True
    return len(name) > 1 and name[1] == ":"  # C:foo


def _check_entry_head(head: bytes) -> None:
    from spoldzielnia_ai.files.validation import looks_executable

    if head.startswith(_ARCHIVE_SIGNATURES) or (len(head) >= 262 and head[257:262] == b"ustar"):
        raise _reject("archive_nested")
    if looks_executable(head):
        raise _reject("archive_contains_executable")


def inspect_zip(path: Path, limits: ArchiveLimits) -> None:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise _reject("archive_corrupt") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise _reject("archive_too_many_entries")

        declared_total = 0
        for info in infos:
            if _unsafe_name(info.filename):
                raise _reject("archive_path_traversal")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise _reject("archive_symlink")
            if info.flag_bits & 0x1:
                raise _reject("archive_encrypted")  # cannot be scanned
            declared_total += info.file_size
            if declared_total > limits.max_total_bytes:
                raise _reject("archive_too_large")
            if info.file_size > _RATIO_MIN_SIZE and info.file_size > limits.max_ratio * max(
                info.compress_size, 1
            ):
                raise _reject("archive_ratio")

        # Second pass: the real bytes, counted while decompressing.
        actual_total = 0
        for info in infos:
            if info.is_dir():
                continue
            produced = 0
            head = b""
            try:
                with archive.open(info) as entry:
                    while chunk := entry.read(_CHUNK):
                        if len(head) < 512:
                            head += chunk[: 512 - len(head)]
                            if len(head) >= 512 or produced == 0:
                                _check_entry_head(head)
                        produced += len(chunk)
                        actual_total += len(chunk)
                        if produced > info.file_size:
                            raise _reject("archive_size_mismatch")
                        if actual_total > limits.max_total_bytes:
                            raise _reject("archive_too_large")
                        if produced > _RATIO_MIN_SIZE and produced > limits.max_ratio * max(
                            info.compress_size, 1
                        ):
                            raise _reject("archive_ratio")
            except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError):
                raise _reject("archive_corrupt") from None
            if head:
                _check_entry_head(head)
