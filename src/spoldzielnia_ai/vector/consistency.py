"""Nightly consistency audit of the canonical index against Postgres (4.4).

Scrolls every point of ``canonical_wiki_index`` and recomputes the payload it should carry
from Postgres (``indexing.canonical_payload``):

- the block is gone, revoked or empty → the point is deleted;
- any governance field differs → the expected payload is written back.

Each batch runs under the index-writer lock (``VectorSync.exclusive``), so the audit can't
write back a state that a concurrent change has already replaced. Anything fixed is reported
as ``VECTOR_DRIFT_DETECTED`` (counts only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle
from spoldzielnia_ai.vector.collections import CANONICAL_COLLECTION
from spoldzielnia_ai.vector.indexing import canonical_payload, indexable
from spoldzielnia_ai.vector.repositories import CanonicalScope, CanonicalVectorRepo

if TYPE_CHECKING:
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.vector.repositories import StoredPoint
    from spoldzielnia_ai.vector.sync import VectorSync

log = get_logger(__name__)

GOVERNANCE_KEYS = (
    "block_id",
    "article_id",
    "author_id",
    "consent_status",
    "is_active",
    "article_status",
    "is_honeypot",
)


@dataclass(frozen=True, slots=True)
class DriftReport:
    scanned: int
    deleted: int
    fixed: int

    @property
    def drifted(self) -> bool:
        return bool(self.deleted or self.fixed)


class ConsistencyAuditor:
    def __init__(self, factory: SessionFactory, client: AsyncQdrantClient, sync: VectorSync):
        self._factory = factory
        self._repo = CanonicalVectorRepo(client)
        self._sync = sync

    async def run(self) -> DriftReport:
        scanned = deleted = fixed = 0
        async for batch in self._repo.scroll():
            scanned += len(batch)
            d, f = await self._check(batch)
            deleted += d
            fixed += f
        report = DriftReport(scanned, deleted, fixed)
        if report.drifted:
            async with self._factory() as session, session.begin():
                await AuditService(session).record(
                    EventType.VECTOR_DRIFT_DETECTED,
                    None,
                    AuditTarget("qdrant_collection", CANONICAL_COLLECTION),
                    {"scanned": scanned, "deleted": deleted, "fixed": fixed},
                )
            log.error("vector_audit.drift", scanned=scanned, deleted=deleted, fixed=fixed)
        else:
            log.info("vector_audit.clean", scanned=scanned)
        return report

    async def _check(self, batch: list[StoredPoint]) -> tuple[int, int]:
        async with self._sync.exclusive() as session:
            ids = [p.id for p in batch]
            blocks = {
                b.id: b
                for b in await session.scalars(select(ArticleBlock).where(ArticleBlock.id.in_(ids)))
            }
            article_ids = {b.article_id for b in blocks.values()}
            articles = {
                a.id: a
                for a in await session.scalars(
                    select(WikiArticle).where(WikiArticle.id.in_(article_ids))
                )
            }
            stale: list[UUID] = []
            fixes: list[tuple[UUID, dict[str, Any]]] = []
            for point in batch:
                block = blocks.get(point.id)
                if block is None or not indexable(block):
                    stale.append(point.id)
                    continue
                expected = canonical_payload(block, articles[block.article_id]).to_payload()
                if any(point.payload.get(k) != expected[k] for k in GOVERNANCE_KEYS):
                    fixes.append((point.id, expected))
            if stale:
                await self._repo.delete(CanonicalScope(block_ids=tuple(stale)))
            for block_id, payload in fixes:
                await self._repo.set_payload(CanonicalScope(block_ids=(block_id,)), payload)
        return len(stale), len(fixes)
