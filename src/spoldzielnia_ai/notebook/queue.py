"""Celery-backed ``ProcessingQueue`` (used by the api and by the egress worker for linked PDFs).

Derivative rows are created PENDING *before* the task is sent, in their own RLS transaction,
so the user sees the status at once and the worker always finds its row.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.notebook.service import NotebookService
from spoldzielnia_ai.workers.celery_adapter import send
from spoldzielnia_ai.workers.jobs import (
    KIND_TASK,
    ArticleJob,
    AtlasIndexJob,
    DerivativeJob,
    PromotionJob,
    TaskName,
)

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.validation import FileKind
    from spoldzielnia_ai.workers.jobs import AtlasJob, ExportJob


def derivative_kinds(kind: FileKind) -> list[DerivativeKind]:
    if kind.category in ("audio", "video"):
        return [DerivativeKind.TRANSCRIPT]
    if kind.category == "image":
        return [DerivativeKind.IMAGE_DESCRIPTION]
    if kind.category in ("document", "text"):
        return [DerivativeKind.PARSED_DOC]
    return []  # archives are kept as files, not processed


class CeleryProcessingQueue:
    def __init__(self, factory: SessionFactory, cipher: EnvelopeCipher, celery: Any) -> None:
        self._factory = factory
        self._cipher = cipher
        self._celery = celery

    async def _send(self, name: TaskName, job: DerivativeJob) -> None:
        # Publishing is a blocking broker round trip.
        await asyncio.to_thread(send, self._celery, name, job)

    async def attachment_stored(
        self, attachment_id: UUID, owner_id: UUID, kind: FileKind
    ) -> list[DerivativeKind]:
        kinds = derivative_kinds(kind)
        if not kinds:
            return []
        async with rls_session(self._factory, owner_id) as session:
            ids = await NotebookService(session, self._cipher).create_derivatives(
                attachment_id, kinds
            )
        for derivative_id, derivative_kind in zip(ids, kinds, strict=True):
            job = DerivativeJob(derivative_id=derivative_id, owner_id=owner_id)
            await self._send(KIND_TASK[derivative_kind], job)
        return kinds

    async def derivative_done(self, job: DerivativeJob) -> None:
        """A derivative has content: screen it for prompt injection (worker-core)."""
        await self._send(TaskName.SCAN_DERIVATIVE, job)

    async def annotations_saved(self, job: DerivativeJob) -> None:
        """A photo's caption and marks changed (9.2): screen, then index, like any derivative."""
        await self._send(TaskName.SCAN_DERIVATIVE, job)

    async def atlas_requested(self, job: AtlasJob) -> None:
        """Prepare a photo for the atlas (9.4, worker-core)."""
        await asyncio.to_thread(send, self._celery, TaskName.PREPARE_ATLAS, job)

    async def export_requested(self, job: ExportJob) -> None:
        """Build a dataset export (9.7, worker-core)."""
        await asyncio.to_thread(send, self._celery, TaskName.EXPORT_ATLAS, job)

    async def atlas_canonical(self, image_id: UUID) -> None:
        """An atlas photo was approved (or must be rebuilt): (re)index it (9.8)."""
        job = AtlasIndexJob(image_id=image_id)
        await asyncio.to_thread(send, self._celery, TaskName.INDEX_ATLAS, job)

    async def recognition_requested(self, job: DerivativeJob) -> None:
        """„Rozpoznaj” on a notebook photo (9.9, worker-core)."""
        await self._send(TaskName.RECOGNIZE_ATLAS, job)

    async def derivative_clean(self, job: DerivativeJob) -> None:
        """Screened and not quarantined: index it for "ask my notebook" (4.1)."""
        await self._send(TaskName.INDEX_DERIVATIVE, job)

    async def promotion_requested(self, promotion_id: UUID, owner_id: UUID) -> None:
        job = PromotionJob(promotion_id=promotion_id, owner_id=owner_id)
        await asyncio.to_thread(send, self._celery, TaskName.PROMOTE_NOTES, job)

    async def article_canonical(self, article_id: UUID) -> None:
        """An article became CANONICAL: (re)index it (4.2)."""
        job = ArticleJob(article_id=article_id)
        await asyncio.to_thread(send, self._celery, TaskName.INDEX_ARTICLE, job)

    async def link_added(self, derivative_id: UUID, owner_id: UUID) -> None:
        job = DerivativeJob(derivative_id=derivative_id, owner_id=owner_id)
        await self._send(TaskName.FETCH_LINK, job)
