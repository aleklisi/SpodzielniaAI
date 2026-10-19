"""Hidden articles for honeypot (canary) blocks (chunk 5.4).

Revision ID: 0011_hidden_articles
Revises: 0010_account_lock
Create Date: 2026-10-19

Canary blocks live in a ``wiki_article`` that is CANONICAL (so its blocks are indexed and
retrievable like any other) but ``is_hidden``: never listed, never served by the Wiki API,
never reviewable, never counted in coverage denominators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0011_hidden_articles"
down_revision: str | None = "0010_account_lock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wiki_article",
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("wiki_article", "is_hidden")
