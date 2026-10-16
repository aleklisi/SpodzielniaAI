"""Core schema: users, private notes, wiki articles/blocks, expert reviews, security audit log.

Revision ID: 0001_core_schema
Revises:
Create Date: 2026-10-16

Tables are created by the owner role. Default privileges from infra/postgres/init/10-roles.sh
give ``spoldzielnia_app`` DML on them. RLS (0002) and audit immutability (0003) follow in 1.4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001_core_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "spoldzielnia_app"  # infra/postgres/init/10-roles.sh

# Types are created/dropped explicitly: `create_table` would not create the type behind
# ARRAY(user_role), and dropping tables never drops types.
user_role = postgresql.ENUM("MEMBER", "EXPERT", "ADMIN", name="user_role", create_type=False)
consent_status = postgresql.ENUM(
    "ACTIVE", "SUSPENDED", "REVOKED", name="consent_status", create_type=False
)
article_status = postgresql.ENUM(
    "DRAFT",
    "IN_REVIEW",
    "CANONICAL",
    "SUPERSEDED",
    "DISPUTED",
    "REVISION_REQUIRED",
    "REJECTED",
    name="article_status",
    create_type=False,
)
review_decision = postgresql.ENUM(
    "APPROVE",
    "REQUEST_CHANGES",
    "REJECT",
    "MARK_DISPUTED",
    name="review_decision",
    create_type=False,
)
ENUMS = (user_role, consent_status, article_status, review_decision)


def _entity_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
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

    # The app role reads the schema revision (/readyz) but must never move it.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {APP_ROLE}")

    op.create_table(
        "app_user",
        sa.Column("oidc_issuer", sa.Text(), nullable=False),
        sa.Column("oidc_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "roles",
            postgresql.ARRAY[str](user_role),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("is_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("risk_score", sa.Double[float](), server_default=sa.text("0"), nullable=False),
        *_entity_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint(
            "oidc_issuer", "oidc_subject", name=op.f("uq_app_user_oidc_issuer_oidc_subject")
        ),
    )

    op.create_table(
        "security_audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # Filled by the hash-chain BEFORE INSERT trigger (0003, chunk 1.4).
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_audit_log")),
    )
    op.create_index(op.f("ix_security_audit_log_actor_id"), "security_audit_log", ["actor_id"])
    op.create_index(op.f("ix_security_audit_log_event_type"), "security_audit_log", ["event_type"])
    op.create_index(
        op.f("ix_security_audit_log_occurred_at"), "security_audit_log", ["occurred_at"]
    )

    op.create_table(
        "wiki_article",
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column[str]("status", article_status, server_default="DRAFT", nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_from_promotion_id", sa.Uuid(), nullable=True),
        *_entity_columns(),
        sa.CheckConstraint(
            "supersedes_id <> id", name=op.f("ck_wiki_article_not_self_superseding")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_wiki_article_version_positive")),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["wiki_article.id"],
            name=op.f("fk_wiki_article_superseded_by_id_wiki_article"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["wiki_article.id"],
            name=op.f("fk_wiki_article_supersedes_id_wiki_article"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wiki_article")),
    )
    op.create_index(op.f("ix_wiki_article_status"), "wiki_article", ["status"])

    op.create_table(
        "article_block",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column[str]("consent_status", consent_status, server_default="ACTIVE", nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("is_honeypot", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_entity_columns(),
        sa.CheckConstraint("position >= 0", name=op.f("ck_article_block_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["wiki_article.id"],
            name=op.f("fk_article_block_article_id_wiki_article"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["app_user.id"], name=op.f("fk_article_block_author_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_article_block")),
        sa.UniqueConstraint(
            "article_id", "position", name=op.f("uq_article_block_article_id_position")
        ),
    )
    op.create_index(op.f("ix_article_block_author_id"), "article_block", ["author_id"])

    op.create_table(
        "expert_review",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column[str]("decision", review_decision, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["article_id"],
            ["wiki_article.id"],
            name=op.f("fk_expert_review_article_id_wiki_article"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["app_user.id"], name=op.f("fk_expert_review_reviewer_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expert_review")),
    )
    op.create_index(op.f("ix_expert_review_article_id"), "expert_review", ["article_id"])
    op.create_index(op.f("ix_expert_review_reviewer_id"), "expert_review", ["reviewer_id"])

    op.create_table(
        "private_note",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("body_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_private_note_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_private_note")),
    )
    op.create_index(op.f("ix_private_note_owner_id"), "private_note", ["owner_id"])


def downgrade() -> None:
    for table in (
        "private_note",
        "expert_review",
        "article_block",
        "wiki_article",
        "security_audit_log",
        "app_user",
    ):
        op.drop_table(table)  # drops the table's indexes and constraints with it
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
