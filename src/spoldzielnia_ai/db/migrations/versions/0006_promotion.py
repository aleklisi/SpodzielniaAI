"""Push-to-wiki promotions (chunk 3.5).

Revision ID: 0006_promotion
Revises: 0005_quarantine
Create Date: 2026-10-16

``promotion`` is private (RLS owner policy, FORCE): it links a member's notes to a wiki draft,
so its source IDs and title hint are stored encrypted. The public side (``wiki_article``)
only references the promotion id (``created_from_promotion_id``, FK added here as planned in 1.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0006_promotion"
down_revision: str | None = "0005_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

promotion_status = postgresql.ENUM(
    "PENDING", "RUNNING", "DONE", "FAILED", name="promotion_status", create_type=False
)
ENUMS = (promotion_status,)


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
    promotion_status.create(op.get_bind())
    op.create_table(
        "promotion",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column[str]("status", promotion_status, server_default="PENDING", nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("sources_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("title_hint_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("source_ids_hash", sa.String(length=64), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("prompt_name", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_digest", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column(
            "injection_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pii_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_promotion_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion")),
        sa.UniqueConstraint("article_id", name=op.f("uq_promotion_article_id")),
    )
    op.create_index(op.f("ix_promotion_owner_id"), "promotion", ["owner_id"])
    op.execute("ALTER TABLE promotion ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE promotion FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY promotion_owner_isolation ON promotion
          USING      (owner_id = {CURRENT_USER})
          WITH CHECK (owner_id = {CURRENT_USER})
        """
    )
    op.create_foreign_key(
        op.f("fk_wiki_article_created_from_promotion_id_promotion"),
        "wiki_article",
        "promotion",
        ["created_from_promotion_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_wiki_article_created_from_promotion_id_promotion"),
        "wiki_article",
        type_="foreignkey",
    )
    op.drop_table("promotion")
    promotion_status.drop(op.get_bind())
