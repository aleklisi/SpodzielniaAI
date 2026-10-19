"""Expert review (chunk 4.2): status changes, quorum, supersession, and their index effects.

Methods work in the caller's transaction and return the ``VectorOp``s to apply **after** it
commits (``vector.sync``). Articles are locked ``FOR UPDATE`` (the indexer's ``FOR SHARE``
waits for us, and we for it), and ``version`` is checked against the version the client saw
and incremented by the ORM on every update (optimistic locking).

Rules:
- Nobody reviews an article they wrote a block of (no self-approval).
- ``settings.governance.review_quorum`` distinct experts must approve. Only approvals given
  since the article's last status change count.
- ``APPROVE`` with ``supersedes_id``: in the same transaction the new article becomes
  CANONICAL and the old one (which must be CANONICAL) SUPERSEDED, linked both ways.
- Every status change is audited (``ARTICLE_STATUS_CHANGED``) in the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import distinct, func, select
from sqlalchemy.orm.exc import StaleDataError

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, ReviewDecision
from spoldzielnia_ai.db.models import ArticleBlock, ExpertReview, WikiArticle
from spoldzielnia_ai.vector.sync import deactivate_articles, reindex_articles
from spoldzielnia_ai.wiki.governance import (
    DECISION_ACTION,
    Action,
    Actor,
    InvalidTransition,
    transition,
)

if TYPE_CHECKING:
    from uuid import UUID

    from pydantic import JsonValue
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.vector.sync import VectorOp


class ReviewError(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class ReviewResult:
    article_id: UUID
    previous: ArticleStatus
    status: ArticleStatus
    version: int
    approvals: int = 0
    quorum: int = 1
    ops: list[VectorOp] = field(default_factory=list["VectorOp"])


async def article_authors(session: AsyncSession, article_id: UUID) -> set[UUID]:
    rows = await session.scalars(
        select(ArticleBlock.author_id).where(ArticleBlock.article_id == article_id).distinct()
    )
    return set(rows)


async def change_status(
    session: AsyncSession,
    article: WikiArticle,
    action: Action,
    actor: Actor,
    actor_id: UUID | None,
    detail: dict[str, JsonValue] | None = None,
) -> list[VectorOp]:
    """Move ``article`` through the state machine, audit it, and return the index operations
    its new status requires. Raises ``InvalidTransition``."""
    source = article.status
    target = transition(source, action, actor)
    article.status = target
    article.status_changed_at = func.now()
    payload: dict[str, JsonValue] = {
        "from": source.value,
        "to": target.value,
        "action": action.value,
        **(detail or {}),
    }
    target_ref = AuditTarget("wiki_article", str(article.id))
    await AuditService(session).record(
        EventType.ARTICLE_STATUS_CHANGED, actor_id, target_ref, payload
    )
    if target == ArticleStatus.CANONICAL:
        return [reindex_articles((article.id,))]
    if source == ArticleStatus.CANONICAL:
        return [deactivate_articles((article.id,), target)]
    return []


class ReviewService:
    def __init__(self, session: AsyncSession, quorum: int = 1) -> None:
        self._session = session
        self._quorum = quorum

    async def _lock(self, *article_ids: UUID) -> dict[UUID, WikiArticle]:
        """Lock in id order, so two transactions locking the same pair can't deadlock."""
        rows = await self._session.scalars(
            select(WikiArticle)
            .where(WikiArticle.id.in_(article_ids))
            .order_by(WikiArticle.id)
            .with_for_update()
        )
        return {row.id: row for row in rows}

    async def _flush(self) -> None:
        try:
            await self._session.flush()
        except StaleDataError:
            raise ReviewError("version_conflict", 409) from None

    async def submit(self, article_id: UUID, actor_id: UUID, is_expert: bool) -> ReviewResult:
        """An author (or, for an article the system sent back, an expert) submits for review."""
        article = (await self._lock(article_id)).get(article_id)
        if article is None:
            raise ReviewError("not_found", 404)
        authors = await article_authors(self._session, article_id)
        if actor_id in authors:
            actor = Actor.AUTHOR
        elif is_expert:
            actor = Actor.EXPERT
        else:
            raise ReviewError("not_found", 404)  # no existence oracle for others' drafts
        active = await self._session.scalar(
            select(func.count())
            .select_from(ArticleBlock)
            .where(
                ArticleBlock.article_id == article_id,
                ArticleBlock.consent_status == ConsentStatus.ACTIVE,
            )
        )
        if not active:
            raise ReviewError("no_content", 409)
        previous = article.status
        try:
            ops = await change_status(self._session, article, Action.SUBMIT, actor, actor_id)
        except InvalidTransition:
            raise ReviewError("invalid_transition", 409) from None
        await self._flush()
        return ReviewResult(article.id, previous, article.status, article.version, ops=ops)

    async def _approvals(self, article: WikiArticle) -> int:
        count = await self._session.scalar(
            select(func.count(distinct(ExpertReview.reviewer_id))).where(
                ExpertReview.article_id == article.id,
                ExpertReview.decision == ReviewDecision.APPROVE,
                ExpertReview.created_at >= article.status_changed_at,
            )
        )
        return int(count or 0)

    async def decide(
        self,
        article_id: UUID,
        reviewer_id: UUID,
        decision: ReviewDecision,
        comment: str | None,
        version: int,
        supersedes_id: UUID | None = None,
    ) -> ReviewResult:
        if supersedes_id is not None and decision != ReviewDecision.APPROVE:
            raise ReviewError("supersedes_requires_approve", 422)
        if supersedes_id == article_id:
            raise ReviewError("supersedes_self", 422)
        ids = (article_id,) if supersedes_id is None else (article_id, supersedes_id)
        locked = await self._lock(*ids)
        article = locked.get(article_id)
        if article is None:
            raise ReviewError("not_found", 404)
        if article.version != version:
            raise ReviewError("version_conflict", 409)
        if reviewer_id in await article_authors(self._session, article_id):
            raise ReviewError("own_article", 403)
        action = DECISION_ACTION[decision]
        previous = article.status
        try:
            transition(previous, action, Actor.EXPERT)  # validate before recording anything
        except InvalidTransition:
            raise ReviewError("invalid_transition", 409) from None
        old: WikiArticle | None = None
        if supersedes_id is not None:
            old = locked.get(supersedes_id)
            if old is None:
                raise ReviewError("supersedes_not_found", 404)
            if old.status != ArticleStatus.CANONICAL:
                raise ReviewError("supersedes_not_canonical", 409)

        self._session.add(
            ExpertReview(
                article_id=article_id, reviewer_id=reviewer_id, decision=decision, comment=comment
            )
        )
        await self._flush()
        approvals = 0
        if action == Action.APPROVE:
            approvals = await self._approvals(article)
            if approvals < self._quorum:
                return ReviewResult(
                    article.id, previous, previous, article.version, approvals, self._quorum
                )

        detail: dict[str, JsonValue] = {"decision": decision.value}
        if old is not None:
            detail["supersedes"] = str(old.id)
        ops = await change_status(self._session, article, action, Actor.EXPERT, reviewer_id, detail)
        article.review_alert = None
        if action == Action.APPROVE:
            article.last_verified_at = func.now()
        if old is not None:
            article.supersedes_id = old.id
            old.superseded_by_id = article.id
            ops += await change_status(
                self._session,
                old,
                Action.SUPERSEDE,
                Actor.SYSTEM,
                reviewer_id,
                {"superseded_by": str(article.id)},
            )
        await self._flush()
        return ReviewResult(
            article.id, previous, article.status, article.version, approvals, self._quorum, ops
        )
