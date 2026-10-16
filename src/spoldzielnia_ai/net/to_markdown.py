"""Fetched documents → Markdown for the notebook (``FETCHED_LINK`` derivatives).

HTML goes through trafilatura's main-content extraction (no scripts, navigation or ads), with
links and images dropped. Plain text and Markdown are decoded as-is. The result is untrusted
data: Stage 3 scans it for prompt injection before any LLM sees it.
"""

from __future__ import annotations

import trafilatura

from spoldzielnia_ai.net.safe_fetch import FetchFailed, FetchResult

MAX_MARKDOWN_CHARS = 500_000


def _decode(body: bytes, charset: str | None) -> str:
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:  # unknown charset label
        return body.decode("utf-8", errors="replace")


def to_markdown(result: FetchResult) -> str:
    if result.content_type in ("text/html", "application/xhtml+xml"):
        markdown = trafilatura.extract(
            _decode(result.body, result.charset),
            url=result.url,
            output_format="markdown",
            include_links=False,
            include_images=False,
            include_comments=False,
            include_tables=True,
        )
        if not markdown:
            raise FetchFailed("no_content")
    elif result.content_type in ("text/plain", "text/markdown"):
        markdown = _decode(result.body, result.charset)
    else:
        raise FetchFailed("content_type_not_allowed")
    return markdown.strip()[:MAX_MARKDOWN_CHARS]
