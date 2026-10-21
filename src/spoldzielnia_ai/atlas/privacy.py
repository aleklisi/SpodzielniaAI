"""People, faces, documents and plates on a photo before it is shared (chunk 9.4, A3).

The local VLM (purpose ``VLM``, ``force_local_llm=True``) answers yes/no per kind. The result is
advisory: the member sees it before sending and the experts see it in review. Without a local
VLM the check reports ``unavailable`` instead of pretending the photo is clean.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from spoldzielnia_ai.llm.gateway import (
    LLMOutputInvalid,
    LLMPurpose,
    LocalModelRejected,
    LocalModelUnavailable,
)
from spoldzielnia_ai.llm.prompts import PRIVACY_CHECK

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMGateway

KINDS = ("people", "faces", "documents", "plates", "addresses")
# Flags that need the member's explicit confirmation before sending.
PEOPLE = frozenset({"people", "faces"})


class PrivacyCheck(BaseModel):
    people: bool = False
    faces: bool = False
    documents: bool = False
    plates: bool = False
    addresses: bool = False


def flagged(flags: dict[str, Any]) -> list[str]:
    return [kind for kind in KINDS if flags.get(kind) is True]


def needs_people_confirmation(flags: dict[str, Any]) -> bool:
    return bool(PEOPLE & set(flagged(flags)))


class PrivacyChecker:
    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    async def check(self, jpeg: bytes) -> dict[str, Any]:
        """``{"check": "ok", "people": bool, …}`` or ``{"check": "unavailable"}``. Other LLM
        errors propagate (the caller retries)."""
        messages, _ = PRIVACY_CHECK.messages([])
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        user: ChatMessage = {
            "role": "user",
            "content": [
                {"type": "text", "text": PRIVACY_CHECK.task},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
        try:
            result = await self._gateway.complete(
                [messages[0], user],
                purpose=LLMPurpose.VLM,
                force_local_llm=True,
                response_model=PrivacyCheck,
            )
        except (LocalModelUnavailable, LocalModelRejected, LLMOutputInvalid):
            return {"check": "unavailable"}
        parsed = result.parsed or PrivacyCheck()
        return {"check": "ok", **parsed.model_dump()}
