"""Work waits for a model instead of failing (chunk 10.2, invariant P5).

Revision ID: 0020_model_wait
Revises: 0019_provider_clearance
Create Date: 2026-10-22

- ``derivative_status`` gains ``WAITING_FOR_MODEL``: no model is configured for the derivative's
  purpose (e.g. image descriptions without a vision model). It resumes when one is.
- ``model_wait``: ids of parked work and the task to send again, with no content. It is not
  under RLS (like ``vector_outbox``), so the resume task can find work of every member
  without a role that bypasses RLS; it resumes each item in its owner's RLS session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0020_model_wait"
down_revision: str | None = "0019_provider_clearance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE derivative_status ADD VALUE IF NOT EXISTS 'WAITING_FOR_MODEL'")
    op.create_table(
        "model_wait",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("task", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_wait")),
        sa.UniqueConstraint("subject_id", "task", name=op.f("uq_model_wait_subject_id_task")),
    )
    op.create_index(op.f("ix_model_wait_purpose"), "model_wait", ["purpose"])


def downgrade() -> None:
    op.drop_index(op.f("ix_model_wait_purpose"), table_name="model_wait")
    op.drop_table("model_wait")
    # Postgres cannot drop an enum value: rebuild the type without it (waiting → FAILED).
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status TYPE text")
    # FORCE RLS binds the owner too: without lifting it this UPDATE silently matches no rows.
    op.execute("ALTER TABLE note_derivative NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE note_derivative SET status = 'FAILED', error = 'model_not_configured' "
        "WHERE status = 'WAITING_FOR_MODEL'"
    )
    op.execute("ALTER TABLE note_derivative FORCE ROW LEVEL SECURITY")
    op.execute("DROP TYPE derivative_status")
    op.execute(
        "CREATE TYPE derivative_status AS ENUM "
        "('PENDING', 'PROCESSING', 'DONE', 'FAILED', 'QUARANTINED')"
    )
    op.execute(
        "ALTER TABLE note_derivative ALTER COLUMN status TYPE derivative_status "
        "USING status::derivative_status"
    )
    op.execute("ALTER TABLE note_derivative ALTER COLUMN status SET DEFAULT 'PENDING'")
