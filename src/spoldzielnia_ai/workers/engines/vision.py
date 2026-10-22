"""Image description through the local VLM of the LLM gateway (chunks 2.5 + 3.4).

The (already metadata-stripped) image is re-encoded to a bounded JPEG and sent inline as a
``data:`` URL. Purpose ``VLM`` is always local, so without a configured local VLM the result
is an explicit ``vision_unavailable``, never a cloud call.
"""

from __future__ import annotations

import asyncio
import base64
import io
from typing import TYPE_CHECKING

from spoldzielnia_ai.llm.gateway import (
    LLMError,
    LLMPurpose,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.llm.prompts import IMAGE_DESCRIPTION
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMGateway

MAX_SIDE = 1280


class UnavailableVisionEngine:
    def extract(self, path: Path, mime_type: str) -> str:
        raise PermanentError("vision_unavailable")


def image_data_url(path: Path) -> str:
    from PIL import Image
    from pillow_heif import register_heif_opener  # pyright: ignore[reportUnknownVariableType]

    register_heif_opener()
    with Image.open(path) as image:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


class GatewayVisionEngine:
    """Runs in a worker thread: each call gets its own event loop for the async gateway."""

    def __init__(self, gateway: Callable[[], LLMGateway]) -> None:
        self._gateway = gateway

    def extract(self, path: Path, mime_type: str) -> str:
        messages, _ = IMAGE_DESCRIPTION.messages([])
        user: ChatMessage = {
            "role": "user",
            "content": [
                {"type": "text", "text": IMAGE_DESCRIPTION.task},
                {"type": "image_url", "image_url": {"url": image_data_url(path)}},
            ],
        }
        try:
            result = asyncio.run(
                self._gateway().complete(
                    [messages[0], user], purpose=LLMPurpose.VLM, private_data=True
                )
            )
        except ModelUnavailable as exc:
            raise PermanentError("vision_unavailable") from exc
        except ModelRejected as exc:
            raise PermanentError("vision_rejected") from exc
        except LLMError as exc:
            raise TransientError(str(exc)) from exc
        text = result.content.strip()
        if not text:
            raise PermanentError("empty_result")
        return text
