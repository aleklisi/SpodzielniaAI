"""Chunk 2.3: metadata stripping (EXIF/GPS, PDF info/XMP, audio tags)."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pikepdf
import pytest
from PIL import Image

from spoldzielnia_ai.files.mutagen_adapter import has_tags
from spoldzielnia_ai.files.sanitize import sanitize
from spoldzielnia_ai.files.validation import ALLOWED, FileRejected
from tests.fixtures import files

if TYPE_CHECKING:
    from pathlib import Path

GPS_IFD, EXIF_IFD = 0x8825, 0x8769
MAKE, MODEL, ORIENTATION, ARTIST = 0x010F, 0x0110, 0x0112, 0x013B
BODY_SERIAL = 0xA431


def _camera_exif(orientation: int = 1) -> Image.Exif:
    exif = Image.Exif()
    exif[MAKE] = "Pszczoła Cam"
    exif[MODEL] = "PC-1"
    exif[ARTIST] = "Jan Kowalski"
    exif[ORIENTATION] = orientation
    exif.get_ifd(EXIF_IFD)[BODY_SERIAL] = "SN-123456"
    gps = exif.get_ifd(GPS_IFD)
    gps[1] = "N"
    gps[2] = (52.0, 13.0, 0.0)
    gps[3] = "E"
    gps[4] = (21.0, 0.0, 30.0)
    return exif


def _run(tmp_path: Path, data: bytes, mime: str, keep_location: bool = False) -> Path:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(data)
    sanitize(src, dst, ALLOWED[mime], keep_location=keep_location, max_pixels=10_000_000)
    return dst


def test_jpeg_loses_gps_and_camera_tags(tmp_path: Path) -> None:
    out = _run(tmp_path, files.jpeg(_camera_exif()), "image/jpeg")
    with Image.open(out) as image:
        exif = image.getexif()
    assert not exif.get_ifd(GPS_IFD)
    assert not exif.get_ifd(EXIF_IFD)
    assert MAKE not in exif
    assert ARTIST not in exif
    assert b"Kowalski" not in out.read_bytes()
    assert b"SN-123456" not in out.read_bytes()


def test_keep_location_retains_only_gps(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(files.jpeg(_camera_exif()))
    result = sanitize(src, dst, ALLOWED["image/jpeg"], keep_location=True, max_pixels=10_000_000)
    assert result.location_retained
    with Image.open(dst) as image:
        exif = image.getexif()
    gps = exif.get_ifd(GPS_IFD)
    assert gps[1] == "N"
    assert gps[3] == "E"
    assert MAKE not in exif
    assert ARTIST not in exif
    assert not exif.get_ifd(EXIF_IFD)
    assert b"SN-123456" not in dst.read_bytes()


def test_keep_location_without_gps_reports_nothing_retained(tmp_path: Path) -> None:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(files.jpeg())
    result = sanitize(src, dst, ALLOWED["image/jpeg"], keep_location=True, max_pixels=10_000_000)
    assert not result.location_retained


def test_orientation_is_applied_before_stripping(tmp_path: Path) -> None:
    """The image is 32x24. EXIF orientation 6 (rotate 90°) must become real pixels: 24x32."""
    out = _run(tmp_path, files.jpeg(_camera_exif(orientation=6)), "image/jpeg")
    with Image.open(out) as image:
        assert image.size == (24, 32)
        assert ORIENTATION not in image.getexif()


def test_png_text_chunks_are_dropped(tmp_path: Path) -> None:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Author", "Jan Kowalski")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "PNG", pnginfo=info)
    out = _run(tmp_path, buffer.getvalue(), "image/png")
    assert b"Kowalski" not in out.read_bytes()


def test_decompression_bomb_image_is_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("1", (5000, 5000)).save(buffer, "PNG")  # 25 Mpx, tiny on disk
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(buffer.getvalue())
    with pytest.raises(FileRejected, match="image_too_many_pixels"):
        sanitize(src, dst, ALLOWED["image/png"], keep_location=False, max_pixels=1_000_000)


def test_pdf_info_and_xmp_are_removed(tmp_path: Path) -> None:
    out = _run(tmp_path, files.pdf(author="Jan Kowalski", title="Pasieka"), "application/pdf")
    with pikepdf.open(out) as pdf:
        assert "/Metadata" not in pdf.Root
        assert not pdf.docinfo.keys()
    assert b"Kowalski" not in out.read_bytes()


def test_encrypted_pdf_is_refused(tmp_path: Path) -> None:
    document = pikepdf.new()
    document.add_blank_page()
    buffer = io.BytesIO()
    document.save(buffer, encryption=pikepdf.Encryption(owner="o", user="u"))
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(buffer.getvalue())
    with pytest.raises(FileRejected, match="pdf_encrypted"):
        sanitize(src, dst, ALLOWED["application/pdf"], keep_location=False, max_pixels=1)


def test_mp3_tags_are_removed(tmp_path: Path) -> None:
    tagged = files.mp3(tags=True)
    src = tmp_path / "tagged"
    src.write_bytes(tagged)
    assert has_tags(src)
    out = _run(tmp_path, tagged, "audio/mpeg")
    assert not has_tags(out)
    assert b"Kowalski" not in out.read_bytes()


def test_text_passes_unchanged(tmp_path: Path) -> None:
    data = "Zażółć\n".encode()
    assert _run(tmp_path, data, "text/plain").read_bytes() == data
