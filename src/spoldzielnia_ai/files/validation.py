"""Allow-list file validation by content, never by name (OWASP file upload).

Rules, in order:

1. Executables are rejected whatever they claim to be. That covers our own signature checks
   (PE ``MZ``, ELF, Mach-O, shebang scripts) and libmagic's executable/script types.
2. The type detected by libmagic must be on the allow-list.
3. The declared extension must belong to the detected type, and so must the declared
   ``Content-Type``, unless it is the generic ``application/octet-stream`` (no claim).
4. ZIP-based types go through the archive guard. OOXML/ODF documents must carry no macros.
5. Text must be valid UTF-8.

Failures raise ``FileRejected`` with a machine-readable ``code`` and the HTTP status to use.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from spoldzielnia_ai.files.archive_guard import ArchiveLimits, inspect_zip
from spoldzielnia_ai.files.magic_adapter import detect_mime

if TYPE_CHECKING:
    from pathlib import Path

Category = Literal["document", "text", "image", "audio", "video", "archive"]

MB = 1024 * 1024
MAX_BYTES: dict[Category, int] = {
    "document": 50 * MB,
    "text": 5 * MB,
    "image": 25 * MB,
    "audio": 200 * MB,
    "video": 200 * MB,
    "archive": 50 * MB,
}
MAX_UPLOAD_BYTES = max(MAX_BYTES.values())


class FileRejected(Exception):
    def __init__(self, code: str, status: int = 415) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class FileKind:
    mime: str  # canonical type stored with the attachment
    extensions: frozenset[str]
    category: Category
    content_types: frozenset[str] = frozenset()  # accepted declared aliases besides `mime`
    container: bool = False  # ZIP-based: archive guard applies

    @property
    def max_bytes(self) -> int:
        return MAX_BYTES[self.category]


def _kind(
    mime: str,
    extensions: str,
    category: Category,
    aliases: str = "",
    container: bool = False,
) -> FileKind:
    return FileKind(
        mime,
        frozenset(extensions.split()),
        category,
        frozenset(aliases.split()),
        container,
    )


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT = "application/vnd.oasis.opendocument.text"

# Keyed by the MIME type libmagic reports.
ALLOWED: dict[str, FileKind] = {
    "application/pdf": _kind("application/pdf", ".pdf", "document"),
    DOCX: _kind(DOCX, ".docx", "document", container=True),
    ODT: _kind(ODT, ".odt", "document", container=True),
    "text/plain": _kind(
        "text/plain", ".txt .md .markdown", "text", "text/markdown text/x-markdown"
    ),
    "image/jpeg": _kind("image/jpeg", ".jpg .jpeg", "image", "image/jpg image/pjpeg"),
    "image/png": _kind("image/png", ".png", "image"),
    "image/webp": _kind("image/webp", ".webp", "image"),
    "image/heic": _kind("image/heic", ".heic .heif", "image", "image/heif"),
    "audio/mpeg": _kind("audio/mpeg", ".mp3", "audio", "audio/mp3"),
    "audio/x-wav": _kind("audio/wav", ".wav", "audio", "audio/x-wav audio/wave audio/vnd.wave"),
    "audio/wav": _kind("audio/wav", ".wav", "audio", "audio/x-wav audio/wave audio/vnd.wave"),
    "audio/ogg": _kind("audio/ogg", ".ogg .oga .opus", "audio", "audio/opus application/ogg"),
    "audio/x-m4a": _kind("audio/mp4", ".m4a", "audio", "audio/x-m4a audio/m4a"),
    "audio/mp4": _kind("audio/mp4", ".m4a .mp4", "audio", "audio/x-m4a audio/m4a"),
    "video/mp4": _kind("video/mp4", ".mp4 .m4a", "video", "audio/mp4 audio/x-m4a"),
    "application/zip": _kind(
        "application/zip", ".zip", "archive", "application/x-zip-compressed", container=True
    ),
}

GENERIC_CONTENT_TYPE = "application/octet-stream"

# libmagic types that are never acceptable, whatever the allow-list says.
_EXECUTABLE_MIMES = frozenset(
    {
        "application/x-dosexec",
        "application/x-msdownload",
        "application/x-executable",
        "application/x-sharedlib",
        "application/x-pie-executable",
        "application/x-mach-binary",
        "application/x-elf",
        "application/x-java-applet",
        "text/x-shellscript",
        "text/x-python",
        "text/x-perl",
        "text/x-php",
        "application/x-bat",
        "application/x-msi",
    }
)
_EXECUTABLE_SIGNATURES = (
    b"MZ",  # PE / DOS
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",  # Mach-O 32
    b"\xfe\xed\xfa\xcf",  # Mach-O 64
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O universal / Java class
    b"#!",  # scripts
)


def looks_executable(head: bytes) -> bool:
    return head.startswith(_EXECUTABLE_SIGNATURES)


def _extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def _check_macros(path: Path, kind: FileKind) -> None:
    with zipfile.ZipFile(path) as archive:
        names = [n.lower() for n in archive.namelist()]
    if kind.mime == DOCX:
        if any(n.endswith("vbaproject.bin") or n.endswith("vbadata.xml") for n in names):
            raise FileRejected("macros_not_allowed")
    elif kind.mime == ODT and any(n.startswith(("basic/", "scripts/")) for n in names):
        raise FileRejected("macros_not_allowed")


def validate_file(
    path: Path,
    filename: str,
    declared_content_type: str | None,
    limits: ArchiveLimits | None = None,
) -> FileKind:
    """Return the ``FileKind`` of the spooled upload at ``path`` or raise ``FileRejected``."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    detected = detect_mime(path)
    if looks_executable(head) or detected in _EXECUTABLE_MIMES:
        raise FileRejected("executable")

    kind = ALLOWED.get(detected)
    if kind is None:
        raise FileRejected("type_not_allowed")
    if _extension(filename) not in kind.extensions:
        raise FileRejected("extension_mismatch")
    declared = (declared_content_type or GENERIC_CONTENT_TYPE).split(";")[0].strip().lower()
    if declared not in {kind.mime, detected, GENERIC_CONTENT_TYPE} | kind.content_types:
        raise FileRejected("content_type_mismatch")
    if path.stat().st_size > kind.max_bytes:
        raise FileRejected("too_large", status=413)

    if kind.container:
        inspect_zip(path, limits or ArchiveLimits())
        _check_macros(path, kind)
    if kind.category == "text":
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise FileRejected("text_not_utf8") from None

    # Markdown and plain text share libmagic's `text/plain`. The extension picks the stored type.
    if kind.category == "text" and _extension(filename) in {".md", ".markdown"}:
        return FileKind("text/markdown", kind.extensions, "text", kind.content_types)
    return kind
