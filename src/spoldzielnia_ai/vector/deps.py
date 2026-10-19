"""Request dependencies for the Qdrant client created by ``create_app``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request
from qdrant_client import AsyncQdrantClient

from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.vector.sync import VectorSync

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue


def get_qdrant(request: Request) -> AsyncQdrantClient:
    return request.app.state.qdrant


QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


def get_vector_sync(request: Request) -> VectorSync:
    """Applies committed governance/consent changes to the canonical index (4.2)."""
    state = request.app.state
    queue: ProcessingQueue = state.processing_queue
    settings: Settings = state.settings
    return VectorSync(
        CanonicalVectorRepo(state.qdrant), queue.article_canonical, settings.qdrant.timeout_seconds
    )


VectorSyncDep = Annotated[VectorSync, Depends(get_vector_sync)]
