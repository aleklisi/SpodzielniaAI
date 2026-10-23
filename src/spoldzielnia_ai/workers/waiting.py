"""Work that waits for a model (10.2, invariant P5).

When a purpose has no usable model (``ModelNotConfigured``), work is parked instead of failing
or retrying: a derivative becomes WAITING_FOR_MODEL, an index job is just recorded, and either
way a ``model_wait`` row names the subject, its owner, the purpose and the task to send again.

``ModelWaitResumer`` runs in worker-core every minute (Celery beat) and right after an admin
activates a configuration or clears a provider. For each purpose that now has a usable model it
sends the parked tasks again and deletes their rows. Derivatives are flipped back to PENDING
in their owner's RLS session first, so the notebook shows them as queued. Nothing is ever sent
to another model in the meantime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.core.metrics import MODEL_WAIT_BACKLOG
from spoldzielnia_ai.db.enums import DerivativeStatus
from spoldzielnia_ai.db.models import ModelWait, NoteDerivative
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.workers.jobs import (
    KIND_TASK,
    ArticleJob,
    AtlasIndexJob,
    DerivativeJob,
    TaskName,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection
    from uuid import UUID

    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import LiteLLMGateway

log = get_logger(__name__)

BATCH = 500
_DERIVATIVE_TASKS = frozenset(t.value for t in KIND_TASK.values())


async def add_wait(
    session: AsyncSession, subject_id: UUID, owner_id: UUID | None, purpose: str, task: str
) -> None:
    """Record parked work (idempotent per subject and task)."""
    statement = (
        pg_insert(ModelWait)
        .values(id=uuid7(), subject_id=subject_id, owner_id=owner_id, purpose=purpose, task=task)
        .on_conflict_do_update(index_elements=["subject_id", "task"], set_={"purpose": purpose})
    )
    await session.execute(statement)


async def park_derivative(
    factory: SessionFactory, derivative_id: UUID, owner_id: UUID, purpose: str
) -> None:
    """WAITING_FOR_MODEL + a ``model_wait`` row for the derivative's own task."""
    async with rls_session(factory, owner_id) as session:
        row = await session.get(NoteDerivative, derivative_id, with_for_update=True)
        if row is None:
            return
        row.status = DerivativeStatus.WAITING_FOR_MODEL
        row.error = f"model_not_configured:{purpose}"[:64]
        await add_wait(session, row.id, owner_id, purpose, KIND_TASK[row.kind].value)
    log.info("worker.waiting_for_model", derivative=str(derivative_id), purpose=purpose)


async def park(
    factory: SessionFactory, subject_id: UUID, owner_id: UUID | None, purpose: str, task: TaskName
) -> None:
    """Record parked work that has no status of its own (index jobs)."""
    async with factory() as session, session.begin():
        await add_wait(session, subject_id, owner_id, purpose, task.value)
    log.info("worker.waiting_for_model", subject=str(subject_id), task=task.value, purpose=purpose)


class ModelWaitResumer:
    def __init__(
        self,
        factory: SessionFactory,
        gateway: LiteLLMGateway,
        send: Callable[[TaskName, BaseModel], Awaitable[None]],
    ) -> None:
        self._factory = factory
        self._gateway = gateway
        self._send = send

    async def run(self, ready_without_route: Collection[LLMPurpose] = ()) -> int:
        """Resume the parked work whose purpose now has a model. Returns how many items.
        ``ready_without_route``: purposes served without a route (the atlas's built-in image
        model, 10.6)."""
        async with self._factory() as session:
            result = await session.execute(
                select(ModelWait.purpose, func.count()).group_by(ModelWait.purpose)
            )
            counts: dict[str, int] = {purpose: count for purpose, count in result.all()}
        for purpose in LLMPurpose:
            MODEL_WAIT_BACKLOG.labels(purpose=purpose.value).set(counts.get(purpose.value, 0))
        if not counts:
            return 0
        await self._gateway.refresh()
        ready = [
            p
            for p in counts
            if p in LLMPurpose.__members__
            and (LLMPurpose(p) in ready_without_route or self._gateway.configured(LLMPurpose(p)))
        ]
        resumed = 0
        for purpose in ready:
            resumed += await self._resume(purpose)
        if resumed:
            log.info("worker.resumed_waiting", items=resumed, purposes=ready)
        return resumed

    async def _resume(self, purpose: str) -> int:
        async with self._factory() as session:
            rows = list(
                await session.scalars(
                    select(ModelWait)
                    .where(ModelWait.purpose == purpose)
                    .order_by(ModelWait.created_at)
                    .limit(BATCH)
                )
            )
        for row in rows:
            job: BaseModel
            if row.task in _DERIVATIVE_TASKS and row.owner_id is not None:
                await self._requeue_derivative(row.subject_id, row.owner_id)
                job = DerivativeJob(derivative_id=row.subject_id, owner_id=row.owner_id)
            elif row.task == TaskName.INDEX_DERIVATIVE.value and row.owner_id is not None:
                job = DerivativeJob(derivative_id=row.subject_id, owner_id=row.owner_id)
            elif row.task == TaskName.INDEX_ARTICLE.value:
                job = ArticleJob(article_id=row.subject_id)
            elif row.task == TaskName.INDEX_ATLAS.value:
                job = AtlasIndexJob(image_id=row.subject_id)
            else:
                log.error("worker.wait_unknown_task", task=row.task)
                continue
            # Send first, then forget: a lost delete only means a harmless second send.
            await self._send(TaskName(row.task), job)
            async with self._factory() as session, session.begin():
                await session.execute(delete(ModelWait).where(ModelWait.id == row.id))
        return len(rows)

    async def _requeue_derivative(self, derivative_id: UUID, owner_id: UUID) -> None:
        async with rls_session(self._factory, owner_id) as session:
            row = await session.get(NoteDerivative, derivative_id, with_for_update=True)
            if row is not None and row.status == DerivativeStatus.WAITING_FOR_MODEL:
                row.status = DerivativeStatus.PENDING
                row.error = None
