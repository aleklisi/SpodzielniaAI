"""``FakeLLMGateway``: no model calls in pytest, ever (conventions §2.3).

Records every call and enforces the private-data rule like the real gateway, **plus** that
callers pass ``private_data=True`` explicitly for sensitive purposes (AGENTS.md §3 rule 4).
``local_available=False`` plays "no cleared model reachable"; ``configured=False`` plays "no
model configured for the purpose at all" (10.2).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.db.enums import IndexGenerationStatus
from spoldzielnia_ai.llm.gateway import (
    PRIVATE_PURPOSES,
    Deployment,
    LLMPurpose,
    LLMResult,
    ModelNotConfigured,
    ModelRejected,
    ModelUnavailable,
)
from spoldzielnia_ai.vector.collections import CANONICAL_COLLECTION, PRIVATE_COLLECTION
from spoldzielnia_ai.vector.generations import Generation, StaticGenerations

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from spoldzielnia_ai.llm.gateway import ChatMessage

_DATA = re.compile(
    r'<untrusted_data id="[0-9a-f]+"[^>]*>\n(.*?)\n</untrusted_data id="[0-9a-f]+">', re.S
)


@dataclass
class Call:
    messages: list[ChatMessage]
    purpose: LLMPurpose
    private_data: bool
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


EMBED_DIM = 32
_WORD = re.compile(r"\w+")


def hash_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Bag of words hashed into ``dim`` buckets, L2-normalised: texts sharing words are close
    under cosine distance, which is all retrieval tests need."""
    vector = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector] if any(vector) else [1.0 / math.sqrt(dim)] * dim


TEST_EMBED = Deployment("test-embed", "test-embed", "http://llm:11434/v1", None, local=True)


def make_generation(
    number: int = 1,
    dim: int = EMBED_DIM,
    status: IndexGenerationStatus = IndexGenerationStatus.SERVING,
    deployment: Deployment | None = TEST_EMBED,
    collections: tuple[str, str] = (PRIVATE_COLLECTION, CANONICAL_COLLECTION),
) -> Generation:
    """An index generation over the bare test collections (``qdrant`` fixture), 10.3."""
    return Generation(number, status, "test-embed", dim, *collections, deployment=deployment)


def static_generations(dim: int = EMBED_DIM) -> StaticGenerations:
    return StaticGenerations((make_generation(dim=dim),))


@dataclass
class FakeLLMGateway:
    reply: Callable[[Call], str] = echo_article
    local_available: bool = True
    configured: bool = True
    rejects: bool = False  # the server answers 4xx (ModelRejected)
    calls: list[Call] = field(default_factory=list[Call])
    embed_dim: int = EMBED_DIM
    embedding: Callable[[str], list[float]] | None = None  # default: hash_embedding
    embedded: list[str] = field(default_factory=list[str])
    embed_models: list[str | None] = field(default_factory=list[str | None])  # per call

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        private_data: bool = False,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult[Any]:
        if purpose in PRIVATE_PURPOSES:
            assert private_data, f"{purpose} must be called with private_data=True"
        call = Call(list(messages), purpose, private_data, response_model)
        self.calls.append(call)
        if not self.configured:
            raise ModelNotConfigured(purpose)
        if not self.local_available:
            raise ModelUnavailable("fake: no cleared model")
        if self.rejects:
            raise ModelRejected("fake: 400")
        content = self.reply(call)
        parsed = response_model.model_validate_json(content) if response_model else None
        return LLMResult(content, parsed, "local-chat", "fake-model")

    async def embed(
        self,
        texts: list[str],
        *,
        private_data: bool = True,
        purpose: LLMPurpose = LLMPurpose.EMBED,
        deployment: Deployment | None = None,
    ) -> list[list[float]]:
        assert private_data or purpose != LLMPurpose.EMBED, (
            "embeddings must be called with private_data=True"
        )
        self.embedded.extend(texts)
        self.embed_models.append(deployment.model if deployment is not None else None)
        if not self.configured:
            raise ModelNotConfigured(purpose)
        if not self.local_available:
            raise ModelUnavailable("fake: no cleared model")
        if self.embedding is not None:
            return [self.embedding(t) for t in texts]
        return [hash_embedding(t, self.embed_dim) for t in texts]
