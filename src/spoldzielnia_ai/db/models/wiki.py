from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, ReviewDecision


class WikiArticle(Entity):
    """A versioned article in the shared wiki, moved through review by the Expert Council.

    ``version`` is the optimistic-locking counter (4.2): every UPDATE checks and increments it,
    so a decision made on a stale view of the article fails instead of overwriting.
    """

    __tablename__ = "wiki_article"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("supersedes_id <> id", name="not_self_superseding"),
    )

    title: Mapped[str] = mapped_column(Text)
    status: Mapped[ArticleStatus] = mapped_column(
        server_default=ArticleStatus.DRAFT.value, default=ArticleStatus.DRAFT, index=True
    )
    supersedes_id: Mapped[UUID | None] = mapped_column(ForeignKey("wiki_article.id"))
    superseded_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("wiki_article.id"))
    version: Mapped[int] = mapped_column(server_default=text("1"), default=1)
    last_verified_at: Mapped[datetime | None]
    created_from_promotion_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("promotion.id", ondelete="SET NULL")
    )
    status_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    review_alert: Mapped[str | None] = mapped_column(Text)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - read by SQLAlchemy


class ArticleBlock(Entity):
    """One block of article text with its provenance (author + consent), AGENTS.md §3.2.

    The matching Qdrant point carries the same ``author_id`` and ``consent_status`` (4.1).
    """

    __tablename__ = "article_block"
    __table_args__ = (
        UniqueConstraint("article_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    article_id: Mapped[UUID] = mapped_column(ForeignKey("wiki_article.id", ondelete="CASCADE"))
    position: Mapped[int]
    author_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    consent_status: Mapped[ConsentStatus] = mapped_column(
        server_default=ConsentStatus.ACTIVE.value, default=ConsentStatus.ACTIVE
    )
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # hex SHA-256 of `content`
    is_honeypot: Mapped[bool] = mapped_column(server_default=text("false"), default=False)


class ExpertReview(Entity):
    """A reviewer's decision on an article. Kept as a governance record, never cascaded away."""

    __tablename__ = "expert_review"

    article_id: Mapped[UUID] = mapped_column(ForeignKey("wiki_article.id"), index=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    decision: Mapped[ReviewDecision]
    comment: Mapped[str | None] = mapped_column(Text)
