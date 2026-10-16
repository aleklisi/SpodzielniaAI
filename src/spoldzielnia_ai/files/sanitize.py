"""Metadata stripping before storage: privacy (no GPS of the apiary, no device serials, no
author names) and less attack surface.

- Images are re-encoded (pixels only, plus the ICC profile) after applying the EXIF
  orientation. With ``keep_location`` only the GPS IFD survives, and every other tag (camera,
  serial, owner, timestamps) is dropped.
- PDF: document info dictionary and XMP metadata removed. Encrypted PDFs are refused.
- Audio/video: all tags removed (ID3, Vorbis comments, MP4 atoms).
- Text, DOCX/ODT and ZIP pass unchanged. Document text is scrubbed for PII in Stage 3.

Sanitising writes a new file. Callers store only its output.
"""

from __future__ import annotations

import shutil
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pikepdf
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener  # pyright: ignore[reportUnknownVariableType]

from spoldzielnia_ai.files.mutagen_adapter import AudioMetadataError, strip_tags
from spoldzielnia_ai.files.validation import FileRejected

if TYPE_CHECKING:
    from pathlib import Path

    from spoldzielnia_ai.files.validation import FileKind

register_heif_opener()

_GPS_IFD = 0x8825
_PIL_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/heic": "HEIF",
}


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    path: Path
    location_retained: bool


def _sanitize_image(
    src: Path, dst: Path, kind: FileKind, keep_location: bool, max_pixels: int
) -> bool:
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(src) as original:
                original.load()
                exif = original.getexif()
                icc = original.info.get("icc_profile")
                image = ImageOps.exif_transpose(original)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise FileRejected("image_too_many_pixels", status=422) from None
    except (OSError, SyntaxError, ValueError):
        raise FileRejected("image_corrupt", status=422) from None

    clean = Image.Exif()
    retained = False
    if keep_location:
        gps = exif.get_ifd(_GPS_IFD)
        if gps:
            clean[_GPS_IFD] = gps  # Pillow serialises the nested IFD
            retained = True

    fmt = _PIL_FORMATS[kind.mime]
    options: dict[str, object] = {"exif": clean.tobytes()} if retained else {}
    if isinstance(icc, bytes):
        options["icc_profile"] = icc
    if fmt == "JPEG":
        options |= {"quality": 92, "optimize": True}
        if image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
    elif fmt == "WEBP":
        options |= {"quality": 92}
    elif fmt == "HEIF":
        options |= {"quality": 90}
    image.save(dst, fmt, **options)
    return retained


def _sanitize_pdf(src: Path, dst: Path) -> None:
    try:
        with pikepdf.open(src) as pdf:
            if pdf.is_encrypted:
                raise FileRejected("pdf_encrypted", status=422)
            if "/Metadata" in pdf.Root:
                del pdf.Root["/Metadata"]
            if "/Info" in pdf.trailer:
                del pdf.trailer["/Info"]
            pdf.save(dst, fix_metadata_version=False)
    except pikepdf.PasswordError:
        raise FileRejected("pdf_encrypted", status=422) from None
    except pikepdf.PdfError:
        raise FileRejected("pdf_corrupt", status=422) from None


def sanitize(
    src: Path, dst: Path, kind: FileKind, *, keep_location: bool, max_pixels: int
) -> SanitizeResult:
    """Write a metadata-free copy of ``src`` to ``dst``."""
    if kind.category == "image":
        retained = _sanitize_image(src, dst, kind, keep_location, max_pixels)
        return SanitizeResult(dst, retained)
    if kind.mime == "application/pdf":
        _sanitize_pdf(src, dst)
        return SanitizeResult(dst, False)
    shutil.copyfile(src, dst)
    if kind.category in ("audio", "video"):
        try:
            strip_tags(dst)
        except AudioMetadataError:
            raise FileRejected("media_corrupt", status=422) from None
    return SanitizeResult(dst, False)
