"""Prompt-injection quarantine for derivatives (chunk 3.3).

Revision ID: 0005_quarantine
Revises: 0004_private_zone
Create Date: 2026-10-16

- ``derivative_status`` gains ``QUARANTINED``: the derivative scored above the injection
  threshold and is excluded from promotion until its owner confirms it.
- ``note_derivative.quarantine_confirmed_at``: when the owner confirmed it after reviewing the
  highlighted spans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005_quarantine"
down_revision: str | None = "0004_private_zone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE derivative_status ADD VALUE IF NOT EXISTS 'QUARANTINED'")
    op.add_column(
        "note_derivative",
        sa.Column("quarantine_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("note_derivative", "quarantine_confirmed_at")
    # Postgres cannot drop an enum value: rebuild the type without it.
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status TYPE text")
    # FORCE RLS binds the owner too: without lifting it this UPDATE silently matches no rows.
    op.execute("ALTER TABLE note_derivative NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE note_derivative SET status = 'DONE' WHERE status = 'QUARANTINED'")
    op.execute("ALTER TABLE note_derivative FORCE ROW LEVEL SECURITY")
    op.execute("DROP TYPE derivative_status")
    op.execute("CREATE TYPE derivative_status AS ENUM ('PENDING', 'PROCESSING', 'DONE', 'FAILED')")
    op.execute(
        "ALTER TABLE note_derivative ALTER COLUMN status TYPE derivative_status "
        "USING status::derivative_status"
    )
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status SET DEFAULT 'PENDING'")
