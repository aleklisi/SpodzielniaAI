"""Request dependencies for the notebook."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from spoldzielnia_ai.core.crypto import EnvelopeCipher
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.files.storage import ObjectStorage
from spoldzielnia_ai.notebook.service import NotebookService


def get_cipher(request: Request) -> EnvelopeCipher:
    return request.app.state.cipher


def get_storage(request: Request) -> ObjectStorage:
    return request.app.state.storage


CipherDep = Annotated[EnvelopeCipher, Depends(get_cipher)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]


def get_notebook(session: RlsSessionDep, cipher: CipherDep) -> NotebookService:
    return NotebookService(session, cipher)


NotebookDep = Annotated[NotebookService, Depends(get_notebook)]
