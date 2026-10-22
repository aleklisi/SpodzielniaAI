"""Request dependencies for the Qdrant client created by ``create_app``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from qdrant_client import AsyncQdrantClient

from spoldzielnia_ai.atlas.vectors import AtlasVectorRepo
from spoldzielnia_ai.vector.repositories import CanonicalFanout, PrivateFanout
from spoldzielnia_ai.vector.sync import AtlasSink, VectorSync

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue
    from spoldzielnia_ai.vector.generations import GenerationSource


def get_qdrant(request: Request) -> AsyncQdrantClient:
    return request.app.state.qdrant


QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


class PrivateIndexes:
    """Deletions from a member's partition of every live index generation (10.3)."""

    def __init__(self, client: AsyncQdrantClient, generations: GenerationSource) -> None:
        self._client = client
        self._generations = generations

    def of(self, user_id: UUID) -> PrivateFanout:
        return PrivateFanout(self._client, self._generations, user_id)


def get_private_indexes(request: Request) -> PrivateIndexes:
    return PrivateIndexes(request.app.state.qdrant, request.app.state.generations)


PrivateIndexesDep = Annotated[PrivateIndexes, Depends(get_private_indexes)]


def get_vector_sync(request: Request) -> VectorSync:
    """Applies committed governance/consent changes to the canonical index (4.2)."""
    state = request.app.state
    queue: ProcessingQueue = state.processing_queue
    settings: Settings = state.settings
    return VectorSync(
        state.session_factory,
        CanonicalFanout(state.qdrant, state.generations),
        queue.article_canonical,
        settings.qdrant.timeout_seconds,
        settings.governance.outbox_alert_after,
        atlas=AtlasSink(AtlasVectorRepo(state.qdrant), queue.atlas_canonical),
    )


VectorSyncDep = Annotated[VectorSync, Depends(get_vector_sync)]
