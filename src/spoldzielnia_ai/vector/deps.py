"""Request dependency for the Qdrant client created by ``create_app``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from qdrant_client import AsyncQdrantClient


def get_qdrant(request: Request) -> AsyncQdrantClient:
    return request.app.state.qdrant


QdrantDep = Annotated[AsyncQdrantClient, Depends(get_qdrant)]
