"""Hidden text in PDFs (chunk 3.3, layer 3): text a human reader cannot see but a parser feeds
to the LLM. Classic indirect prompt injection.

Per character (pdfium via pypdfium2, Apache/BSD; PyMuPDF is AGPL):

- render mode 3 (``invisible``) or 7 (clip only),
- fill colour near white (all channels ≥ 240) or fully transparent (alpha 0),
- font size below 2 pt,
- glyph box outside the page's media box.

Consecutive hidden characters form spans. Callers remove the span texts from the parsed output
and keep only metadata (page, reason, length), never the text itself.

Typed adapter around pypdfium2's ctypes bindings (conventions §2.2).
"""

from __future__ import annotations

import ctypes
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

Reason = Literal["invisible_render", "white_text", "transparent_text", "tiny_font", "off_page"]
Box = tuple[float, float, float, float]

_INVISIBLE_MODES = {3, 7}
_WHITE = 240
_TINY_PT = 2.0
_MAX_PAGES = 500


@dataclass(frozen=True, slots=True)
class HiddenSpan:
    page: int  # 1-based
    reason: Reason
    text: str


@dataclass
class _Runs:
    """Groups consecutive hidden characters (with the spaces between them) into spans."""

    page: int
    spans: list[HiddenSpan] = field(default_factory=list[HiddenSpan])
    _chars: list[str] = field(default_factory=list[str])
    _reason: Reason | None = None

    def add(self, char: str, reason: Reason | None) -> None:
        if char.isspace() and self._chars:
            self._chars.append(char)
            return
        if reason != self._reason:
            self.flush()
            self._reason = reason
        if reason is not None:
            self._chars.append(char)

    def flush(self) -> None:
        text = "".join(self._chars).strip()
        if self._reason is not None and text:
            self.spans.append(HiddenSpan(self.page, self._reason, text))
        self._chars, self._reason = [], None


def _char_reason(raw: Any, text_page: Any, index: int, box: Box) -> Reason | None:
    text_object = raw.FPDFText_GetTextObject(text_page, index)
    if text_object and raw.FPDFTextObj_GetTextRenderMode(text_object) in _INVISIBLE_MODES:
        return "invisible_render"
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if raw.FPDFText_GetFillColor(text_page, index, r, g, b, a):
        if a.value == 0:
            return "transparent_text"
        if min(r.value, g.value, b.value) >= _WHITE:
            return "white_text"
    size = float(raw.FPDFText_GetFontSize(text_page, index))
    if 0 < size < _TINY_PT:
        return "tiny_font"
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    if raw.FPDFText_GetCharBox(text_page, index, left, right, bottom, top):
        x0, y0, x1, y1 = box
        if right.value < x0 or left.value > x1 or top.value < y0 or bottom.value > y1:
            return "off_page"
    return None


def find_hidden_text(path: Path) -> list[HiddenSpan]:
    import pypdfium2 as pdfium  # pyright: ignore[reportMissingTypeStubs]
    import pypdfium2.raw  # pyright: ignore[reportMissingTypeStubs]

    raw: Any = pypdfium2.raw
    spans: list[HiddenSpan] = []
    document: Any = pdfium.PdfDocument(str(path))
    try:
        for page_index in range(min(len(document), _MAX_PAGES)):
            page: Any = document[page_index]
            text_page: Any = page.get_textpage()
            try:
                x0, y0, x1, y1 = (float(v) for v in page.get_mediabox())
                runs = _Runs(page_index + 1)
                for index in range(int(raw.FPDFText_CountChars(text_page.raw))):
                    char = chr(int(raw.FPDFText_GetUnicode(text_page.raw, index)))
                    runs.add(char, _char_reason(raw, text_page.raw, index, (x0, y0, x1, y1)))
                runs.flush()
                spans.extend(runs.spans)
            finally:
                text_page.close()
                page.close()
    finally:
        document.close()
    return spans


def remove_hidden(text: str, spans: list[HiddenSpan]) -> tuple[str, int]:
    """Remove every hidden span from the parsed text (whitespace-insensitive).

    Returns the text and the number of removed occurrences. Spans the parser dropped or
    rewrote beyond whitespace are not found, and the flag still records them.
    """
    removed = 0
    for span in sorted(spans, key=lambda s: len(s.text), reverse=True):
        words = span.text.split()
        if not words:
            continue
        pattern = re.compile(r"\s*".join(re.escape(w) for w in words))
        text, n = pattern.subn("", text)
        removed += n
    return text, removed
