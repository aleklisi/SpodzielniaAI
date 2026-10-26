"""The organisation's logo and favicons from an uploaded image (chunk 13.2, rule R3).

Only PNG, JPEG and WebP are accepted, decided by content (``files.validation``, libmagic), not
by name. SVG is refused: it can carry script and external references. The stored files are
re-encoded from pixels only: no EXIF, no text chunks, no ICC profile, never the original.

- ``logo.png``: fits 512x128 (never upscaled), transparency kept;
- ``favicon-32.png`` and ``favicon-180.png``: the logo padded to a square on transparency.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

from spoldzielnia_ai.files.validation import MB, FileRejected, validate_file

if TYPE_CHECKING:
    from pathlib import Path

LOGO_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_LOGO_BYTES = 2 * MB
MAX_SIDE = 4096
LOGO_BOX = (512, 128)
FAVICON_SIZES = (32, 180)


@dataclass(frozen=True, slots=True)
class LogoFiles:
    logo: bytes
    favicons: dict[int, bytes]  # side → PNG
    sha256: str  # of the stored logo.png

    def files(self) -> dict[str, bytes]:
        """Stored file name → bytes."""
        return {
            "logo.png": self.logo,
            **{f"favicon-{side}.png": data for side, data in self.favicons.items()},
        }


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)  # no pnginfo, no icc_profile: pixels only
    return out.getvalue()


def render_logo(path: Path, filename: str, content_type: str | None) -> LogoFiles:
    """Validate the spooled upload and render the stored files, or raise ``FileRejected``."""
    if path.stat().st_size > MAX_LOGO_BYTES:
        raise FileRejected("too_large", status=413)
    kind = validate_file(path, filename, content_type)
    if kind.mime not in LOGO_TYPES:
        raise FileRejected("logo_type")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size  # from the header, before any pixel is decoded
                if width > MAX_SIDE or height > MAX_SIDE or width < 1 or height < 1:
                    raise FileRejected("dimensions", status=422)
                image.load()
                rgba = ImageOps.exif_transpose(image).convert("RGBA")
    except FileRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise FileRejected("dimensions", status=422) from None
    except (OSError, ValueError, SyntaxError):
        raise FileRejected("unreadable_image", status=422) from None

    logo = rgba.copy()
    logo.thumbnail(LOGO_BOX, Image.Resampling.LANCZOS)
    logo_png = _png(logo)

    side = max(rgba.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
    favicons = {
        size: _png(square.resize((size, size), Image.Resampling.LANCZOS)) for size in FAVICON_SIZES
    }
    return LogoFiles(logo_png, favicons, hashlib.sha256(logo_png).hexdigest())
