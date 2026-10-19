"""Read-time re-validation of canonical hits against Postgres (gap G17).

Qdrant may briefly lag behind Postgres (an outbox operation waiting for retry). Every hit is
therefore checked against the source of truth before anything is read for it: the block must
still be ACTIVE and hold content, and its article must still be CANONICAL. ``RAGService``
(5.1) runs every search through ``load_retrievable``, and turns the hits that failed into
repair operations (``repair_ops``) for the outbox, closing the window for the next query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - dataclass field type

from sqlalchemy import select

from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle
from spoldzielnia_ai.vector.repositories import CanonicalScope
from spoldzielnia_ai.vector.sync import OpKind, VectorOp

if TYPE_CHECKING:
    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.vector.repositories import CanonicalHit


def _retrievable(ids: list[UUID]) -> Select[ArticleBlock]:
    return (
        select(ArticleBlock)
        .join(WikiArticle, WikiArticle.id == ArticleBlock.article_id)
        .where(
            ArticleBlock.id.in_(ids),
            ArticleBlock.consent_status == ConsentStatus.ACTIVE,
            ArticleBlock.content.is_not(None),
            WikiArticle.status == ArticleStatus.CANONICAL,
        )
    )


async def revalidate(session: AsyncSession, hits: list[CanonicalHit]) -> list[CanonicalHit]:
    """The hits that are still retrievable according to Postgres, in their original order."""
    if not hits:
        return []
    valid = {b.id for b in await session.scalars(_retrievable([h.block_id for h in hits]))}
    return [h for h in hits if h.block_id in valid]


@dataclass(frozen=True, slots=True)
class LiveBlock:
    """A hit that passed re-validation, with its text from Postgres."""

    block_id: UUID
    article_id: UUID
    author_id: UUID
    content: str
    is_honeypot: bool
    score: float


@dataclass(frozen=True, slots=True)
class Revalidated:
    live: list[LiveBlock]
    stale: list[UUID]  # retrievable in Qdrant, not according to Postgres


async def load_retrievable(session: AsyncSession, hits: list[CanonicalHit]) -> Revalidated:
    """Re-validate ``hits`` and load their text, keeping the hit order."""
    if not hits:
        return Revalidated([], [])
    rows = {b.id: b for b in await session.scalars(_retrievable([h.block_id for h in hits]))}
    live: list[LiveBlock] = []
    stale: list[UUID] = []
    for hit in hits:
        block = rows.get(hit.block_id)
        if block is None or block.content is None:
            stale.append(hit.block_id)
            continue
        live.append(
            LiveBlock(
                block.id,
                block.article_id,
                block.author_id,
                block.content,
                block.is_honeypot,
                hit.score,
            )
        )
    return Revalidated(live, stale)


async def repair_ops(session: AsyncSession, block_ids: list[UUID]) -> list[VectorOp]:
    """Operations that bring the points of ``block_ids`` in line with Postgres.

    Run in the transaction that records them: the rows are locked ``FOR SHARE``, so a consent
    or review change either committed before (and is what we read) or waits and records its
    own operation after ours (a higher ``seq``).
    """
    if not block_ids:
        return []
    rows = list(
        await session.execute(
            select(ArticleBlock, WikiArticle)
            .join(WikiArticle, WikiArticle.id == ArticleBlock.article_id)
            .where(ArticleBlock.id.in_(block_ids))
            .with_for_update(read=True)
        )
    )
    found = {block.id for block, _ in rows}
    deleted = [b for b in block_ids if b not in found]
    groups: dict[tuple[str, str], list[UUID]] = {}
    for block, article in rows:
        if block.consent_status == ConsentStatus.REVOKED or block.content is None:
            deleted.append(block.id)
            continue
        key = (block.consent_status.value, article.status.value)
        groups.setdefault(key, []).append(block.id)
    ops: list[VectorOp] = []
    if deleted:
        ops.append(VectorOp(OpKind.DELETE, CanonicalScope(block_ids=tuple(deleted))))
    for (consent, status), ids in groups.items():
        active = consent == ConsentStatus.ACTIVE and status == ArticleStatus.CANONICAL
        ops.append(
            VectorOp(
                OpKind.SET_PAYLOAD,
                CanonicalScope(block_ids=tuple(ids)),
                {"consent_status": consent, "article_status": status, "is_active": active},
            )
        )
    return ops
