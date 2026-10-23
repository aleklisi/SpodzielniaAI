"""OpenDocument text (ODT) → Markdown without any model (10.5).

``content.xml`` is read from the (already scanned and archive-checked) zip with defusedxml and a
size cap. Headings, paragraphs, lists and tables become Markdown; styles, images and frames are
ignored.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from spoldzielnia_ai.workers.engines.base import PermanentError

if TYPE_CHECKING:
    from pathlib import Path
    from xml.etree.ElementTree import Element

MAX_CONTENT_BYTES = 50 * 1024 * 1024

_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _inline(element: Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if child.tag == f"{_TEXT}s":
            parts.append(" " * int(child.get(f"{_TEXT}c", "1") or "1"))
        elif child.tag == f"{_TEXT}tab":
            parts.append("\t")
        elif child.tag == f"{_TEXT}line-break":
            parts.append("\n")
        elif child.tag != f"{_TEXT}note":  # footnote bodies would interrupt the sentence
            parts.append(_inline(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _cell(element: Element) -> str:
    return " ".join(_inline(p).strip() for p in element.iter(f"{_TEXT}p")).replace("|", "\\|")


def _table(element: Element) -> list[str]:
    rows = [
        [_cell(c) for c in row.iter(f"{_TABLE}table-cell")]
        for row in element.iter(f"{_TABLE}table-row")
    ]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    lines = ["| " + " | ".join(r + [""] * (width - len(r))) + " |" for r in rows]
    lines.insert(1, "|" + " --- |" * width)
    return ["\n".join(lines)]


def _blocks(element: Element, depth: int = 0) -> list[str]:
    out: list[str] = []
    for child in element:
        tag = child.tag
        if tag == f"{_TEXT}h":
            level = min(int(child.get(f"{_TEXT}outline-level", "1") or "1"), 6)
            if text := _inline(child).strip():
                out.append(f"{'#' * level} {text}")
        elif tag == f"{_TEXT}p":
            if text := _inline(child).strip():
                out.append(text)
        elif tag == f"{_TEXT}list":
            items: list[str] = []
            for item in child.findall(f"{_TEXT}list-item"):
                for block in _blocks(item, depth + 1):
                    marker = "" if block.startswith("  ") else "- "
                    items.append("  " * depth + marker + block if marker else block)
            if items:
                out.append("\n".join(items))
        elif tag == f"{_TABLE}table":
            out.extend(_table(child))
        elif tag in (f"{_TEXT}section", f"{_TEXT}list-header"):
            out.extend(_blocks(child, depth))
    return out


def odt_to_markdown(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("content.xml")
            if info.file_size > MAX_CONTENT_BYTES:
                raise PermanentError("document_too_large")
            root = ElementTree.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, DefusedXmlException) as exc:
        raise PermanentError("document_unparseable") from exc
    body = root.find(f"{_OFFICE}body/{_OFFICE}text")
    if body is None:
        raise PermanentError("document_unparseable")
    return "\n\n".join(_blocks(body))
