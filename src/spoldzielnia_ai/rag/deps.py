"""Request dependencies for answering questions (5.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from spoldzielnia_ai.rag.private import NotebookQA
from spoldzielnia_ai.vector.embedding import Embedder

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings


def get_embedder(request: Request) -> Embedder:
    """Embeddings with the index generations' models (10.3, ``app.state.generations``)."""
    return Embedder(request.app.state.llm, request.app.state.generations)


def get_notebook_qa(request: Request) -> NotebookQA:
    state = request.app.state
    settings: Settings = state.settings
    return NotebookQA(state.qdrant, get_embedder(request), state.llm, settings.rag)


NotebookQADep = Annotated[NotebookQA, Depends(get_notebook_qa)]
