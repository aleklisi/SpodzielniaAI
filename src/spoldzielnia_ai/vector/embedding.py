"""Embeddings through the gateway, with the model of an index generation (4.1, 10.3).

Always ``private_data=True``: private notes and canonical knowledge go only to embedding
models cleared for private data (AGENTS.md rule 4). Each generation is embedded with the model
that built it (``Generation.deployment``), and a query only ever searches the generation whose
model embedded it (``Query``): two vector spaces are never mixed (P8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from spoldzielnia_ai.llm.gateway import LLMError, LLMPurpose, ModelNotConfigured

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.gateway import LLMGateway
    from spoldzielnia_ai.vector.generations import Generation, GenerationSource

BATCH_SIZE = 16


class EmbeddingMismatch(LLMError):
    """The embedding model returned the wrong number or size of vectors."""


@dataclass(frozen=True, slots=True)
class Query:
    """Query vectors and the generation they may search (and no other)."""

    generation: Generation
    vectors: list[list[float]]

    @property
    def vector(self) -> list[float]:
        return self.vectors[0]


class Embedder:
    def __init__(
        self, gateway: LLMGateway, generations: GenerationSource, batch_size: int = BATCH_SIZE
    ) -> None:
        self._gateway = gateway
        self.generations = generations
        self._batch_size = batch_size

    async def serving(self) -> Generation:
        """The generation searches use; ``ModelNotConfigured`` if there is none yet."""
        generation = await self.generations.serving()
        if generation is None:
            raise ModelNotConfigured(LLMPurpose.EMBED)
        return generation

    async def writable(self) -> list[Generation]:
        """The generations indexing writes to; ``ModelNotConfigured`` if there is none."""
        generations = await self.generations.writable()
        if not generations:
            raise ModelNotConfigured(LLMPurpose.EMBED)
        return generations

    async def query(self, texts: list[str]) -> Query:
        generation = await self.serving()
        return Query(generation, await self.embed_with(generation, texts))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """With the serving generation's model."""
        return (await self.query(texts)).vectors

    async def embed_with(self, generation: Generation, texts: list[str]) -> list[list[float]]:
        if generation.deployment is None:
            raise ModelNotConfigured(LLMPurpose.EMBED)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            out = await self._gateway.embed(
                batch, private_data=True, deployment=generation.deployment
            )
            if len(out) != len(batch):
                raise EmbeddingMismatch(f"{len(out)} vectors for {len(batch)} texts")
            for vector in out:
                if len(vector) != generation.dimension:
                    raise EmbeddingMismatch(
                        f"vector size {len(vector)}, generation {generation.number} has "
                        f"{generation.dimension}"
                    )
            vectors.extend(out)
        return vectors
