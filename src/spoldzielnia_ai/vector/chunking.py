"""Splitting long private texts into chunks for embedding (canonical blocks are embedded whole)."""

from __future__ import annotations

import re

MAX_CHARS = 1500
OVERLAP = 200

_PARAGRAPH = re.compile(r"\n\s*\n")
_JOIN = "\n\n"


def _pieces(text: str, max_chars: int) -> list[str]:
    """Paragraphs, with any paragraph longer than ``max_chars`` cut at whitespace."""
    pieces: list[str] = []
    for paragraph in _PARAGRAPH.split(text):
        paragraph = paragraph.strip()
        while len(paragraph) > max_chars:
            cut = paragraph.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    return pieces


def chunk_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    """Paragraphs packed into chunks of at most ``max_chars``. Each chunk after the first starts
    with up to ``overlap`` characters from the end of the previous one."""
    if overlap + len(_JOIN) >= max_chars // 2:
        raise ValueError("overlap must be well below max_chars")
    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, max_chars - overlap - len(_JOIN)):
        candidate = f"{current}{_JOIN}{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(current)
        tail = current[-overlap:]
        space = tail.find(" ")
        tail = tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail
        current = f"{tail}{_JOIN}{piece}" if overlap else piece
    if current:
        chunks.append(current)
    return chunks
