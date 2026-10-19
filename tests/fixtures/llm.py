"""``FakeLLMGateway``: no model calls in pytest, ever (conventions §2.3).

Records every call and enforces the local-only rule like the real gateway, **plus** that
callers pass ``force_local_llm=True`` explicitly for sensitive purposes (AGENTS.md §3.4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.llm.gateway import (
    ALWAYS_LOCAL,
    LLMResult,
    LocalModelRejected,
    LocalModelUnavailable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMPurpose

_DATA = re.compile(
    r'<untrusted_data id="[0-9a-f]+"[^>]*>\n(.*?)\n</untrusted_data id="[0-9a-f]+">', re.S
)


@dataclass
class Call:
    messages: list[ChatMessage]
    purpose: LLMPurpose
    force_local_llm: bool
    response_model: type[BaseModel] | None

    @property
    def text(self) -> str:
        parts: list[str] = []
        for message in self.messages:
            content = message["content"]
            if isinstance(content, str):
                parts.append(content)
            else:
                parts.extend(p["text"] for p in content if p["type"] == "text")
        return "\n".join(parts)

    def untrusted_blocks(self) -> list[str]:
        return _DATA.findall(self.text)


def echo_article(call: Call) -> str:
    """Structured echo: one block per untrusted document, verbatim (worst case for leakage)."""
    import json

    blocks = [{"heading": None, "text": block} for block in call.untrusted_blocks()]
    return json.dumps({"title": "Szkic artykułu", "blocks": blocks}, ensure_ascii=False)


@dataclass
class FakeLLMGateway:
    reply: Callable[[Call], str] = echo_article
    local_available: bool = True
    rejects: bool = False  # the server answers 4xx (LocalModelRejected)
    calls: list[Call] = field(default_factory=list[Call])

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult[Any]:
        if purpose in ALWAYS_LOCAL:
            assert force_local_llm, f"{purpose} must be called with force_local_llm=True"
        call = Call(list(messages), purpose, force_local_llm, response_model)
        self.calls.append(call)
        if not self.local_available:
            raise LocalModelUnavailable("fake: no local model")
        if self.rejects:
            raise LocalModelRejected("fake: 400")
        content = self.reply(call)
        parsed = response_model.model_validate_json(content) if response_model else None
        return LLMResult(content, parsed, "local-chat", "fake-model")

    async def embed(self, texts: list[str], *, force_local_llm: bool = True) -> list[list[float]]:
        return [[float(len(t))] for t in texts]
