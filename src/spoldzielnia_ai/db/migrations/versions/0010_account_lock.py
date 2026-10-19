"""Why and when an account was locked (chunk 5.2).

Revision ID: 0010_account_lock
Revises: 0009_vector_outbox
Create Date: 2026-10-19

The anti-distillation shield locks accounts (coverage budget, honeypots, risk). An admin
reviewing the lock needs the reason without digging through the audit log: a short
machine-readable code (``coverage_7d``, ``honeypot`` …), never query content.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0010_account_lock"
down_revision: str | None = "0009_vector_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("locked_reason", sa.String(64), nullable=True))
    op.add_column("app_user", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("app_user", "locked_at")
    op.drop_column("app_user", "locked_reason")
