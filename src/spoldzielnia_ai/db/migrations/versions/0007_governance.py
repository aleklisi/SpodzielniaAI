"""Expert review bookkeeping (chunk 4.2).

Revision ID: 0007_governance
Revises: 0006_promotion
Create Date: 2026-10-19

- ``wiki_article.status_changed_at``: when the article last changed status. Approvals count
  towards the quorum only if given after it, so approvals from an earlier review round
  never carry over.
- ``wiki_article.review_alert``: why the article is in the review queue when the system put it
  there (e.g. a co-author withdrew consent, 4.3). Cleared by the next expert decision.
- ``wiki_article.version`` (from 0001) becomes the optimistic-locking counter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0007_governance"
down_revision: str | None = "0006_promotion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wiki_article",
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("wiki_article", sa.Column("review_alert", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("wiki_article", "review_alert")
    op.drop_column("wiki_article", "status_changed_at")
