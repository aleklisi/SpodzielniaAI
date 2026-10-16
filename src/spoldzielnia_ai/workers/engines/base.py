"""Engine protocol and the error taxonomy the task runner relies on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class PermanentError(Exception):
    """Retrying will not help (unsupported or corrupt input, engine unavailable). The
    derivative becomes FAILED with ``code`` shown to the user."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransientError(Exception):
    """Worth retrying with backoff (storage or database hiccup, IdP-like outages)."""


class Engine(Protocol):
    """Turns a decrypted file into text (Markdown for documents)."""

    def extract(self, path: Path, mime_type: str) -> str: ...
