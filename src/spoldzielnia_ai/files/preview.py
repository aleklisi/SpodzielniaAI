"""Display renditions of stored (already sanitised) images, chunk 9.2.

Browsers can't show HEIC and phones shouldn't download 20 MB originals to draw boxes, so every
image gets a bounded JPEG preview. Renditions are re-encoded from pixels only: no metadata, not
even the GPS a member chose to keep on the original.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image
from pillow_heif import register_heif_opener  # pyright: ignore[reportUnknownVariableType]

from spoldzielnia_ai.files.validation import FileRejected

if TYPE_CHECKING:
    from pathlib import Path

register_heif_opener()

PREVIEW_SIDE = 1600
FULL_SIDE = 4096
# Formats every browser displays as stored; anything else is served as a JPEG rendition.
BROWSER_FORMATS = frozenset({"image/jpeg", "image/png", "image/webp"})


@dataclass(frozen=True, slots=True)
class Rendition:
    data: bytes
    width: int  # of the source image
    height: int


def render_jpeg(src: Path, max_side: int, max_pixels: int, quality: int = 85) -> Rendition:
    """A JPEG of at most ``max_side`` px on the long side (never upscaled)."""
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(src) as image:
                width, height = image.size
                image.draft("RGB", (max_side, max_side))  # cheap JPEG downscale on decode
                rgb = image.convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise FileRejected("image_too_many_pixels", status=422) from None
    except (OSError, SyntaxError, ValueError):
        raise FileRejected("image_corrupt", status=422) from None
    rgb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    rgb.save(buffer, "JPEG", quality=quality, optimize=True)
    return Rendition(buffer.getvalue(), width, height)
