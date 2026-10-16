"""Image description through the local VLM of the LLM gateway (Stage 3.4).

Until the gateway exists, images get an explicit ``vision_unavailable`` failure instead of a
fake description.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.workers.engines.base import PermanentError

if TYPE_CHECKING:
    from pathlib import Path


class UnavailableVisionEngine:
    def extract(self, path: Path, mime_type: str) -> str:
        raise PermanentError("vision_unavailable")
