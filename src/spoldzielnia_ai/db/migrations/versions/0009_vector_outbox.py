"""Transactional outbox for canonical-index operations (chunk 4.4, gap G17).

Revision ID: 0009_vector_outbox
Revises: 0008_consent
Create Date: 2026-10-19

A governance or consent change inserts its Qdrant operations here in the same transaction, so
an operation can't be lost between the commit and Qdrant. The API applies them right after
commit, and the reconciler (Celery beat) retries whatever is left, in ``seq`` order (an
identity column: assigned at insert, so it follows commit order for conflicting changes).
Not private (no RLS): rows hold article/block/author ids and governance fields only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0009_vector_outbox"
down_revision: str | None = "0008_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

vector_op_kind = postgresql.ENUM(
    "UPSERT", "SET_PAYLOAD", "DELETE", name="vector_op_kind", create_type=False
)
outbox_status = postgresql.ENUM("PENDING", "DONE", name="outbox_status", create_type=False)
ENUMS = (vector_op_kind, outbox_status)


def _timestamps() -> list[sa.Column[Any]]:
    return [
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
    ]


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)
    op.create_table(
        "vector_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column[str]("op", vector_op_kind, nullable=False),
        sa.Column("filter_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column[str]("status", outbox_status, server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("alerted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vector_outbox")),
        sa.UniqueConstraint("seq", name=op.f("uq_vector_outbox_seq")),
    )
    op.create_index(op.f("ix_vector_outbox_status"), "vector_outbox", ["status", "seq"])


def downgrade() -> None:
    op.drop_table("vector_outbox")
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
