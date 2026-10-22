"""Embeddings through the gateway, always on the local model (``private_data=True``, 4.1).

Both private notes and canonical knowledge are embedded on the server: neither leaves it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.llm.gateway import LLMError

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.gateway import LLMGateway

BATCH_SIZE = 16


class EmbeddingMismatch(LLMError):
    """The embedding model returned the wrong number or size of vectors."""


class Embedder:
    def __init__(self, gateway: LLMGateway, dimension: int, batch_size: int = BATCH_SIZE) -> None:
        self._gateway = gateway
        self.dimension = dimension
        self._batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            out = await self._gateway.embed(batch, private_data=True)
            if len(out) != len(batch):
                raise EmbeddingMismatch(f"{len(out)} vectors for {len(batch)} texts")
            for vector in out:
                if len(vector) != self.dimension:
                    raise EmbeddingMismatch(
                        f"vector size {len(vector)}, qdrant.vector_size is {self.dimension}"
                    )
            vectors.extend(out)
        return vectors
