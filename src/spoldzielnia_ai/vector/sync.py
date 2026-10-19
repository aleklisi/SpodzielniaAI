"""Applying governance and consent changes to the canonical index (4.2 to 4.4, gap G17).

Postgres is the source of truth. A change commits there first, then its ``VectorOp``s are
applied to Qdrant synchronously (``wait=True``, bounded timeout), so a block is no longer
retrievable when the HTTP response is sent. Ops are plain data (JSON), so a failed one can be
stored and retried later.

- ``SET_PAYLOAD``: overwrite payload keys of the points in ``scope``.
- ``DELETE``: delete the points in ``scope``.
- ``UPSERT``: (re)index the articles in ``scope.article_ids``. Embedding is slow, so the API
  hands it to the ``index`` queue, and the indexer reads the current state itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ConsentStatus
from spoldzielnia_ai.vector.repositories import CanonicalScope

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from uuid import UUID

    from spoldzielnia_ai.db.enums import ArticleStatus
    from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo

log = get_logger(__name__)


class OpKind(StrEnum):
    UPSERT = "UPSERT"
    SET_PAYLOAD = "SET_PAYLOAD"
    DELETE = "DELETE"


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


class VectorSync:
    def __init__(
        self,
        repo: CanonicalVectorRepo,
        reindex: Callable[[UUID], Awaitable[None]],
        timeout_seconds: float,
    ) -> None:
        self._repo = repo
        self._reindex = reindex
        self._timeout = timeout_seconds

    async def apply_one(self, op: VectorOp) -> None:
        """Apply ``op`` or raise. Every op is idempotent, so retrying it is safe."""
        async with asyncio.timeout(self._timeout):
            match op.kind:
                case OpKind.SET_PAYLOAD:
                    await self._repo.set_payload(op.scope, op.payload)
                case OpKind.DELETE:
                    await self._repo.delete(op.scope)
                case OpKind.UPSERT:
                    for article_id in op.scope.article_ids or ():
                        await self._reindex(article_id)

    async def apply(self, ops: list[VectorOp]) -> bool:
        """Apply ``ops`` in order, stopping at the first failure. Returns True if all applied.
        Failures are logged, never raised: the change is already committed in Postgres, and
        retrieval re-validates against Postgres."""
        for op in ops:
            try:
                await self.apply_one(op)
            except Exception as exc:
                log.error("vector_sync.failed", kind=op.kind.value, error=type(exc).__name__)
                return False
        return True
