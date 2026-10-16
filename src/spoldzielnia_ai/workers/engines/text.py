"""Plain text and Markdown need no ML: decode and pass on."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TextEngine:
    def extract(self, path: Path, mime_type: str) -> str:
        return path.read_text(encoding="utf-8", errors="replace")
