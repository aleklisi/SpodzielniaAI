"""Exit Strategy (chunk 4.3): a member suspends, resumes or revokes their own contributions.

``ConsentService.change`` works in the caller's transaction and touches only blocks whose
``author_id`` is the caller (scope enforcement is in the query, not in a later check). Articles
are locked before blocks, the same order as the indexer, so the two can't deadlock.

- SUSPEND: ACTIVE → SUSPENDED. The text stays, hidden, and the points become inactive.
- RESUME: SUSPENDED → ACTIVE. The points become active only where the article is still
  CANONICAL. A degraded article returns to retrieval only through a new expert approval.
- REVOKE: ACTIVE/SUSPENDED → REVOKED. Text and hash are wiped (tombstone row), points deleted.

Cascade (suspend and revoke): an affected CANONICAL article that still has content by other
authors is degraded to REVISION_REQUIRED. All its points leave retrieval and the review queue
gets the alert below. An article left with no content by anyone after a revoke is withdrawn
(REJECTED) and disappears from the wiki.

The returned ``VectorOp``s must be applied after commit (``vector.sync``).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle
from spoldzielnia_ai.vector.repositories import CanonicalScope
from spoldzielnia_ai.vector.sync import delete_blocks, resume_blocks, suspend_blocks
from spoldzielnia_ai.wiki.governance import REVIEWABLE, TRANSITIONS, Action, Actor
from spoldzielnia_ai.wiki.review import change_status

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from pydantic import JsonValue
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.vector.sync import VectorOp

# A code, rendered in the reader's language by the frontend (14.1).
REVISION_ALERT = "revision_required"
# The count runs inside the transaction: it must not hold the row locks for long.
COUNT_TIMEOUT_SECONDS = 2.0


class ConsentAction(StrEnum):
    SUSPEND = "SUSPEND"
    RESUME = "RESUME"
    REVOKE = "REVOKE"


SOURCE: dict[ConsentAction, frozenset[ConsentStatus]] = {
    ConsentAction.SUSPEND: frozenset({ConsentStatus.ACTIVE}),
    ConsentAction.RESUME: frozenset({ConsentStatus.SUSPENDED}),
    ConsentAction.REVOKE: frozenset({ConsentStatus.ACTIVE, ConsentStatus.SUSPENDED}),
}
TARGET: dict[ConsentAction, ConsentStatus] = {
    ConsentAction.SUSPEND: ConsentStatus.SUSPENDED,
    ConsentAction.RESUME: ConsentStatus.ACTIVE,
    ConsentAction.REVOKE: ConsentStatus.REVOKED,
}
EVENT: dict[ConsentAction, EventType] = {
    ConsentAction.SUSPEND: EventType.CONSENT_SUSPENDED,
    ConsentAction.RESUME: EventType.CONSENT_RESUMED,
    ConsentAction.REVOKE: EventType.CONSENT_REVOKED,
}

# Best-effort count of the Qdrant points a change will touch, for the audit record.
type PointCounter = Callable[[CanonicalScope], Awaitable[int | None]]


@dataclass(frozen=True, slots=True)
class ConsentResult:
    action: ConsentAction
    blocks: int
    articles: list[UUID]
    degraded: list[UUID] = field(default_factory=list["UUID"])
    withdrawn: list[UUID] = field(default_factory=list["UUID"])
    ops: list[VectorOp] = field(default_factory=list["VectorOp"])


class ConsentService:
    def __init__(self, session: AsyncSession, count_points: PointCounter | None = None) -> None:
        self._session = session
        self._count_points = count_points

    async def _affected_articles(
        self, action: ConsentAction, author_id: UUID, article_ids: list[UUID] | None
    ) -> list[UUID]:
        query = select(ArticleBlock.article_id).where(
            ArticleBlock.author_id == author_id,
            ArticleBlock.consent_status.in_(SOURCE[action]),
        )
        if article_ids is not None:
            query = query.where(ArticleBlock.article_id.in_(article_ids))
        return sorted(set(await self._session.scalars(query.distinct())))

    async def change(
        self, action: ConsentAction, author_id: UUID, article_ids: list[UUID] | None = None
    ) -> ConsentResult:
        affected = await self._affected_articles(action, author_id, article_ids)
        if not affected:
            return ConsentResult(action, 0, [])
        articles = {
            a.id: a
            for a in await self._session.scalars(
                select(WikiArticle)
                .where(WikiArticle.id.in_(affected))
                .order_by(WikiArticle.id)
                .with_for_update()
            )
        }
        blocks = list(
            await self._session.scalars(
                select(ArticleBlock)
                .where(ArticleBlock.article_id.in_(affected))
                .order_by(ArticleBlock.article_id, ArticleBlock.position)
                .with_for_update()
            )
        )

        target = TARGET[action]
        changed: Counter[UUID] = Counter()
        for block in blocks:
            if block.author_id != author_id or block.consent_status not in SOURCE[action]:
                continue
            block.consent_status = target
            if action == ConsentAction.REVOKE:
                block.content = None
                block.content_hash = None
            changed[block.article_id] += 1
        if not changed:  # changed by a concurrent request between the two queries
            return ConsentResult(action, 0, [])
        scope = tuple(article_ids) if article_ids is not None else None
        points = await self._points(author_id, scope)

        ops: list[VectorOp] = []
        degraded: list[UUID] = []
        withdrawn: list[UUID] = []
        if action == ConsentAction.SUSPEND:
            ops.append(suspend_blocks(author_id, scope))
        elif action == ConsentAction.REVOKE:
            ops.append(delete_blocks(author_id, scope))
        else:
            canonical = tuple(a for a in changed if articles[a].status == ArticleStatus.CANONICAL)
            others = tuple(a for a in changed if a not in canonical)
            if canonical:
                ops.append(resume_blocks(author_id, canonical, activate=True))
            if others:
                ops.append(resume_blocks(author_id, others, activate=False))

        if action != ConsentAction.RESUME:
            for article_id in changed:
                article = articles[article_id]
                co_authored = any(
                    b.article_id == article_id
                    and b.author_id != author_id
                    and b.consent_status != ConsentStatus.REVOKED
                    for b in blocks
                )
                emptied = all(
                    b.consent_status == ConsentStatus.REVOKED
                    for b in blocks
                    if b.article_id == article_id
                )
                detail: dict[str, JsonValue] = {
                    "reason": f"consent_{action.value.lower()}",
                    "blocks_withdrawn": changed[article_id],
                }
                if co_authored and article.status == ArticleStatus.CANONICAL:
                    ops += await change_status(
                        self._session, article, Action.DEGRADE, Actor.SYSTEM, author_id, detail
                    )
                    article.review_alert = REVISION_ALERT
                    degraded.append(article_id)
                    await AuditService(self._session).record(
                        EventType.ARTICLE_DEGRADED,
                        author_id,
                        AuditTarget("wiki_article", str(article_id)),
                        detail,
                    )
                elif co_authored and article.status in REVIEWABLE:
                    article.review_alert = REVISION_ALERT  # experts see it before deciding
                elif emptied and (article.status, Action.WITHDRAW) in TRANSITIONS:
                    ops += await change_status(
                        self._session, article, Action.WITHDRAW, Actor.SYSTEM, author_id, detail
                    )
                    withdrawn.append(article_id)

        payload: dict[str, JsonValue] = {
            "blocks": sum(changed.values()),
            "articles": len(changed),
            "scope": "selected" if article_ids is not None else "all",
            "points": points,
            "degraded": len(degraded),
            "withdrawn": len(withdrawn),
        }
        await AuditService(self._session).record(
            EVENT[action], author_id, AuditTarget("app_user", str(author_id)), payload
        )
        await self._session.flush()
        return ConsentResult(
            action, sum(changed.values()), sorted(changed), degraded, withdrawn, ops
        )

    async def _points(self, author_id: UUID, scope: tuple[UUID, ...] | None) -> int | None:
        if self._count_points is None:
            return None
        try:
            async with asyncio.timeout(COUNT_TIMEOUT_SECONDS):
                return await self._count_points(
                    CanonicalScope(author_id=author_id, article_ids=scope)
                )
        except Exception:
            return None  # Qdrant down: the change still goes ahead (Postgres is the truth)
