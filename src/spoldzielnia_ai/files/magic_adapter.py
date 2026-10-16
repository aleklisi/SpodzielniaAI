"""Typed adapter around ``python-magic`` / libmagic (conventions §2.2).

Detection reads the whole file (``from_file``): OOXML and ODF are recognised from their ZIP
directory, not from the first bytes alone.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import magic

if TYPE_CHECKING:
    from pathlib import Path

# libmagic handles are not thread-safe. One per thread (uploads run in a thread pool).
_local = threading.local()


def _detector() -> magic.Magic:
    detector: magic.Magic | None = getattr(_local, "detector", None)
    if detector is None:
        detector = magic.Magic(mime=True)
        _local.detector = detector
    return detector


def detect_mime(path: Path) -> str:
    detected = _detector().from_file(str(path))  # pyright: ignore[reportUnknownMemberType]
    return str(detected).strip().lower()
