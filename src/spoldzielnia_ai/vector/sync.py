"""Applying governance and consent changes to the canonical index (4.2 to 4.4, gap G17).

Postgres is the source of truth. A change records its ``VectorOp``s in the ``vector_outbox``
in its own transaction (``VectorSync.record``). After the commit, ``VectorSync.flush`` applies
them to Qdrant synchronously (``wait=True``, bounded timeout), so a block is no longer
retrievable when the HTTP response is sent. Whatever fails stays in the outbox, and the
reconciler (``drain``, Celery beat every 30 s) retries it with exponential backoff.

Ordering: every drain holds one advisory lock and applies pending rows in ``seq`` order,
stopping at the first failure. A later operation never overtakes an earlier one on the same
points (say, a "resume" applied before the "suspend" it undoes).

- ``SET_PAYLOAD``: overwrite payload keys of the points in ``scope``.
- ``DELETE``: delete the points in ``scope``.
- ``UPSERT``: (re)index the articles in ``scope.article_ids``. Embedding is slow, so the API
  hands it to the ``index`` queue, and the indexer reads the current state itself.

Generations (10.3): in production the canonical writer is ``CanonicalFanout``, which applies
SET_PAYLOAD and DELETE to every live generation (read fresh, under the index-writer lock), so a
consent change made while a new embedding model is being built reaches both versions.

Atlas photos (9.8) share the outbox: their rows carry ``filter_json.target = "atlas"`` and go to
``AtlasSink`` (the ``atlas_visual_index`` repository and its re-index hand-off), in the same
``seq`` order as the wiki's operations.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from spoldzielnia_ai.atlas.vectors import TARGET as ATLAS_TARGET
from spoldzielnia_ai.atlas.vectors import AtlasOp, AtlasScope
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ConsentStatus, OutboxStatus, VectorOpKind
from spoldzielnia_ai.db.models import VectorOutbox
from spoldzielnia_ai.vector.repositories import CanonicalScope

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.db.enums import ArticleStatus
    from spoldzielnia_ai.db.session import SessionFactory

log = get_logger(__name__)


OpKind = VectorOpKind

DRAIN_BATCH = 100
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 600
DONE_RETENTION = timedelta(days=7)
_LOCK = select(func.pg_advisory_xact_lock(func.hashtextextended("vector_outbox", 0)))
_LOCK_NOT_AVAILABLE = "55P03"


@dataclass(frozen=True, slots=True)
class VectorOp:
    kind: OpKind
    scope: CanonicalScope
    payload: dict[str, Any] = field(default_factory=dict[str, Any])

    def __post_init__(self) -> None:
        if self.kind == OpKind.SET_PAYLOAD and not self.payload:
            raise ValueError("SET_PAYLOAD needs a payload")
        if self.kind == OpKind.UPSERT and not self.scope.article_ids:
            raise ValueError("UPSERT works on whole articles")

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "scope": self.scope.to_json(), "payload": self.payload}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> VectorOp:
        return cls(
            OpKind(data["kind"]),
            CanonicalScope.from_json(data["scope"]),
            dict(data.get("payload") or {}),
        )


# --- The operations governance and consent produce ---------------------------------------------


def reindex_articles(article_ids: tuple[UUID, ...]) -> VectorOp:
    return VectorOp(OpKind.UPSERT, CanonicalScope(article_ids=article_ids))


def deactivate_articles(article_ids: tuple[UUID, ...], status: ArticleStatus) -> VectorOp:
    """The articles left CANONICAL: none of their points is retrievable any more."""
    return VectorOp(
        OpKind.SET_PAYLOAD,
        CanonicalScope(article_ids=article_ids),
        {"article_status": status.value, "is_active": False},
    )


def suspend_blocks(author_id: UUID, article_ids: tuple[UUID, ...] | None) -> VectorOp:
    return VectorOp(
        OpKind.SET_PAYLOAD,
        CanonicalScope(author_id=author_id, article_ids=article_ids),
        {"consent_status": ConsentStatus.SUSPENDED.value, "is_active": False},
    )


def resume_blocks(
    author_id: UUID, article_ids: tuple[UUID, ...] | None, *, activate: bool
) -> VectorOp:
    """Consent is ACTIVE again. The points become retrievable only if ``activate`` (their
    article is still CANONICAL)."""
    payload: dict[str, Any] = {"consent_status": ConsentStatus.ACTIVE.value}
    if activate:
        payload["is_active"] = True
    return VectorOp(
        OpKind.SET_PAYLOAD, CanonicalScope(author_id=author_id, article_ids=article_ids), payload
    )


def delete_blocks(author_id: UUID, article_ids: tuple[UUID, ...] | None) -> VectorOp:
    return VectorOp(OpKind.DELETE, CanonicalScope(author_id=author_id, article_ids=article_ids))


# --- Applying them -----------------------------------------------------------------------------


class CanonicalWriter(Protocol):
    """``CanonicalFanout`` (every live generation) or one ``CanonicalVectorRepo``."""

    async def set_payload(self, scope: CanonicalScope, payload: Mapping[str, Any]) -> None: ...

    async def delete(self, scope: CanonicalScope) -> None: ...


class AtlasWriter(Protocol):
    """Every live atlas generation (``AtlasFanout``, 10.6) or one collection (tests)."""

    async def set_payload(self, scope: AtlasScope, payload: Mapping[str, Any]) -> None: ...

    async def delete(self, scope: AtlasScope) -> None: ...


@dataclass(frozen=True, slots=True)
class AtlasSink:
    """Where atlas operations go: the writer and the re-index hand-off (worker-core)."""

    repo: AtlasWriter
    reindex: Callable[[UUID], Awaitable[None]]


type AnyOp = VectorOp | AtlasOp


def _op_from_row(row: VectorOutbox) -> AnyOp:
    if row.filter_json.get("target") == ATLAS_TARGET:
        return AtlasOp(row.op, AtlasScope.from_json(row.filter_json), dict(row.payload_json))
    return VectorOp(row.op, CanonicalScope.from_json(row.filter_json), dict(row.payload_json))


@dataclass(frozen=True, slots=True)
class DrainResult:
    applied: int = 0
    failed: bool = False  # stopped at an operation that failed (it stays pending)
    busy: bool = False  # another drain held the lock for longer than the timeout
    remaining: int = 0  # pending rows left in the drained range


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_MAX_SECONDS))


def _lock_timeout(exc: DBAPIError) -> bool:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE or "lock timeout" in str(orig)


class VectorSync:
    def __init__(
        self,
        factory: SessionFactory,
        repo: CanonicalWriter,
        reindex: Callable[[UUID], Awaitable[None]],
        timeout_seconds: float,
        alert_after: int = 5,
        atlas: AtlasSink | None = None,
    ) -> None:
        self._factory = factory
        self._repo = repo
        self._reindex = reindex
        self._timeout = timeout_seconds
        self._alert_after = alert_after
        self._atlas = atlas

    async def apply_one(self, op: AnyOp) -> None:
        """Apply ``op`` or raise. Every op is idempotent, so retrying it is safe."""
        if isinstance(op, AtlasOp):
            await self._apply_atlas(op)
            return
        async with asyncio.timeout(self._timeout):
            match op.kind:
                case OpKind.SET_PAYLOAD:
                    await self._repo.set_payload(op.scope, op.payload)
                case OpKind.DELETE:
                    await self._repo.delete(op.scope)
                case OpKind.UPSERT:
                    for article_id in op.scope.article_ids or ():
                        await self._reindex(article_id)

    async def _apply_atlas(self, op: AtlasOp) -> None:
        if self._atlas is None:
            raise RuntimeError("no atlas sink configured")
        async with asyncio.timeout(self._timeout):
            match op.kind:
                case OpKind.SET_PAYLOAD:
                    await self._atlas.repo.set_payload(op.scope, op.payload)
                case OpKind.DELETE:
                    await self._atlas.repo.delete(op.scope)
                case OpKind.UPSERT:
                    for image_id in op.scope.image_ids or ():
                        await self._atlas.reindex(image_id)

    @staticmethod
    async def record(
        session: AsyncSession, ops: list[VectorOp] | list[AtlasOp] | list[AnyOp]
    ) -> list[int]:
        """Add ``ops`` to the outbox in the caller's transaction. Returns their ``seq``s."""
        if not ops:
            return []
        rows = [
            {
                "id": uuid7(),
                "op": op.kind,
                "filter_json": op.scope.to_json(),
                "payload_json": op.payload,
            }
            for op in ops
        ]
        result = await session.execute(
            insert(VectorOutbox).returning(VectorOutbox.seq, sort_by_parameter_order=True), rows
        )
        return list(result.scalars())

    async def flush(self, seqs: list[int]) -> bool:
        """After commit: apply the outbox up to the last of ``seqs`` (older pending rows
        first). Returns True if all of them are applied. Never raises: the change is
        committed, and the reconciler finishes the job."""
        if not seqs:
            return True
        try:
            result = await self.drain(up_to=max(seqs))
        except Exception as exc:
            log.error("vector_sync.flush_failed", error=type(exc).__name__)
            return False
        return result.remaining == 0 and not result.busy

    async def drain(self, up_to: int | None = None, *, due_only: bool = False) -> DrainResult:
        """Apply pending rows in ``seq`` order (up to ``up_to``), stopping at the first failure.
        With ``due_only`` (the reconciler), nothing runs while the oldest row is in backoff."""
        try:
            async with self.exclusive() as session:
                return await self._drain_locked(session, up_to, due_only)
        except DBAPIError as exc:
            if _lock_timeout(exc):
                return DrainResult(busy=True)
            raise

    @asynccontextmanager
    async def exclusive(self) -> AsyncGenerator[AsyncSession]:
        """A transaction holding the index-writer lock: outbox drains and the consistency
        audit never interleave their Qdrant writes. Waits at most the sync timeout (raises
        ``DBAPIError`` with SQLSTATE 55P03 after that)."""
        async with self._factory() as session, session.begin():
            lock_ms = int(self._timeout * 1000)
            await session.execute(text(f"SET LOCAL lock_timeout = '{lock_ms}ms'"))
            await session.execute(_LOCK)
            yield session

    async def _drain_locked(
        self, session: AsyncSession, up_to: int | None, due_only: bool
    ) -> DrainResult:
        query = (
            select(VectorOutbox)
            .where(VectorOutbox.status == OutboxStatus.PENDING)
            .order_by(VectorOutbox.seq)
        )
        if up_to is not None:
            query = query.where(VectorOutbox.seq <= up_to)
        rows = list(await session.scalars(query.limit(DRAIN_BATCH)))
        now = await session.scalar(select(func.now()))
        assert now is not None  # noqa: S101 - now() is never NULL
        if due_only and rows and rows[0].next_attempt_at > now:
            return DrainResult(remaining=len(rows))
        applied, failed = 0, False
        for row in rows:
            op = _op_from_row(row)
            try:
                await self.apply_one(op)
            except Exception as exc:
                failed = True
                row.attempts += 1
                row.last_error = type(exc).__name__[:64]
                row.next_attempt_at = now + backoff(row.attempts)
                log.warning(
                    "vector_outbox.apply_failed",
                    seq=row.seq,
                    kind=row.op.value,
                    attempts=row.attempts,
                    error=row.last_error,
                )
                if row.attempts >= self._alert_after and not row.alerted:
                    row.alerted = True
                    log.error(
                        "vector_outbox.stuck", seq=row.seq, kind=row.op.value, attempts=row.attempts
                    )
                break
            row.status = OutboxStatus.DONE
            applied += 1
        return DrainResult(applied, failed, remaining=len(rows) - applied)

    async def reconcile(self) -> DrainResult:
        """Reconciler (Celery beat): drain due rows batch by batch, then drop old DONE rows."""
        total = 0
        while True:
            result = await self.drain(due_only=True)
            total += result.applied
            if result.failed or result.busy or result.applied < DRAIN_BATCH:
                break
        async with self._factory() as session, session.begin():
            await session.execute(
                delete(VectorOutbox).where(
                    VectorOutbox.status == OutboxStatus.DONE,
                    VectorOutbox.updated_at < func.now() - DONE_RETENTION,
                )
            )
        if total or result.failed:
            log.info("vector_outbox.reconciled", applied=total, failed=result.failed)
        return DrainResult(total, result.failed, result.busy, result.remaining)


async def outbox_backlog(factory: SessionFactory) -> int:
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(VectorOutbox)
            .where(VectorOutbox.status == OutboxStatus.PENDING)
        )
    return int(count or 0)
