"""Read-time re-validation of canonical hits against Postgres (gap G17).

Qdrant may briefly lag behind Postgres (an outbox operation waiting for retry). Every hit is
therefore checked against the source of truth before anything is read for it: the block must
still be ACTIVE and hold content, and its article must still be CANONICAL. ``RAGService``
(5.1) runs every search through this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.vector.repositories import CanonicalHit


async def revalidate(session: AsyncSession, hits: list[CanonicalHit]) -> list[CanonicalHit]:
    """The hits that are still retrievable according to Postgres, in their original order."""
    if not hits:
        return []
    valid = set(
        await session.scalars(
            select(ArticleBlock.id)
            .join(WikiArticle, WikiArticle.id == ArticleBlock.article_id)
            .where(
                ArticleBlock.id.in_([h.block_id for h in hits]),
                ArticleBlock.consent_status == ConsentStatus.ACTIVE,
                ArticleBlock.content.is_not(None),
                WikiArticle.status == ArticleStatus.CANONICAL,
            )
        )
    )
    return [h for h in hits if h.block_id in valid]
