"""Visual atlas: domains and label vocabulary (chunk 9.1).

Revision ID: 0014_visual_labels
Revises: 0013_user_admin
Create Date: 2026-10-21

Shared zone (no RLS): labels are cooperative knowledge. No domain-specific seed: admins create
domains, experts fill them (``spoldzielnia atlas seed-example`` loads a demo domain outside
production).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0014_visual_labels"
down_revision: str | None = "0013_user_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

visual_domain_status = postgresql.ENUM(
    "ACTIVE", "ARCHIVED", name="visual_domain_status", create_type=False
)
visual_label_status = postgresql.ENUM(
    "PROPOSED", "ACTIVE", "DEPRECATED", "REJECTED", name="visual_label_status", create_type=False
)
ENUMS = (visual_domain_status, visual_label_status)


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


def _user_fk(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=op.f(f"fk_{table}_{column}_app_user"), ondelete="SET NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)

    op.create_table(
        "visual_domain",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column[str]("status", visual_domain_status, server_default="ACTIVE", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        *_timestamps(),
        _user_fk("created_by", "visual_domain"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visual_domain")),
        sa.UniqueConstraint("slug", name=op.f("uq_visual_domain_slug")),
    )

    op.create_table(
        "visual_label",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column(
            "synonyms", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("search_key", sa.Text(), nullable=False),
        sa.Column[str]("status", visual_label_status, server_default="ACTIVE", nullable=False),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["visual_domain.id"],
            name=op.f("fk_visual_label_domain_id_visual_domain"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["visual_label.id"],
            name=op.f("fk_visual_label_merged_into_id_visual_label"),
        ),
        _user_fk("proposed_by", "visual_label"),
        _user_fk("approved_by", "visual_label"),
        sa.CheckConstraint("merged_into_id <> id", name=op.f("ck_visual_label_not_self_merged")),
        sa.CheckConstraint(
            "(merged_into_id IS NULL) OR status = 'DEPRECATED'",
            name=op.f("ck_visual_label_merged_is_deprecated"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visual_label")),
        sa.UniqueConstraint("domain_id", "slug", name=op.f("uq_visual_label_domain_id_slug")),
    )
    op.create_index(op.f("ix_visual_label_domain_id"), "visual_label", ["domain_id"])


def downgrade() -> None:
    op.drop_table("visual_label")
    op.drop_table("visual_domain")
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
