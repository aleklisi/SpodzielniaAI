"""The shared visual atlas and contributions to it (chunk 9.4).

Revision ID: 0016_atlas
Revises: 0015_image_annotation
Create Date: 2026-10-21

- ``atlas_submission`` (private, RLS FORCE): links a member's annotated photo to the atlas
  photo made from it, so only its owner sees which private photo it came from.
- ``atlas_image`` (shared): the copy sent to the atlas. Block-level provenance (A4):
  ``author_id`` + ``consent_status``; a REVOKED photo is a tombstone (no objects, no text).
  Objects are encrypted under the row's own DEK. ``show_author`` and ``allow_export`` are the
  author's choices (A12, A9).
- ``atlas_region`` (shared): a mark with its own ``author_id`` + ``consent_status``, and
  ``corrected_by`` when an expert changed it.
- ``atlas_review`` (shared): expert decisions (9.5), kept as a governance record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0016_atlas"
down_revision: str | None = "0015_image_annotation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

atlas_submission_status = postgresql.ENUM(
    "PENDING",
    "PREPARED",
    "SENT",
    "WITHDRAWN",
    "FAILED",
    name="atlas_submission_status",
    create_type=False,
)
atlas_image_status = postgresql.ENUM(
    "DRAFT",
    "IN_REVIEW",
    "APPROVED",
    "REVISION_REQUIRED",
    "REJECTED",
    name="atlas_image_status",
    create_type=False,
)
ENUMS = (atlas_submission_status, atlas_image_status)
# Existing types, not created here.
consent_status = postgresql.ENUM(name="consent_status", create_type=False)
review_decision = postgresql.ENUM(name="review_decision", create_type=False)

BOX_CHECK = (
    "x >= 0 AND y >= 0 AND w >= 0.005 AND h >= 0.005 AND x + w <= 1.000001 AND y + h <= 1.000001"
)


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


def _user_fk(column: str, table: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=op.f(f"fk_{table}_{column}_app_user"), ondelete=ondelete
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)

    op.create_table(
        "atlas_image",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column[str]("consent_status", consent_status, server_default="ACTIVE", nullable=False),
        sa.Column[str]("status", atlas_image_status, server_default="DRAFT", nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=64), nullable=True),
        sa.Column("preview_object_key", sa.String(length=64), nullable=True),
        sa.Column("thumb_object_key", sa.String(length=64), nullable=True),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("phash", sa.String(length=16), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column(
            "privacy_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("allow_export", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("show_author", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("review_alert", sa.Text(), nullable=True),
        *_timestamps(),
        _user_fk("author_id", "atlas_image"),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["visual_domain.id"],
            name=op.f("fk_atlas_image_domain_id_visual_domain"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_atlas_image_version_positive")),
        sa.CheckConstraint(
            "(consent_status = 'REVOKED') = (object_key IS NULL AND caption IS NULL "
            "AND preview_object_key IS NULL AND thumb_object_key IS NULL)",
            name=op.f("ck_atlas_image_revoked_is_empty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_atlas_image")),
        sa.UniqueConstraint("object_key", name=op.f("uq_atlas_image_object_key")),
    )
    op.create_index(op.f("ix_atlas_image_author_id"), "atlas_image", ["author_id"])
    op.create_index(op.f("ix_atlas_image_status"), "atlas_image", ["status"])
    op.create_index(op.f("ix_atlas_image_domain_id"), "atlas_image", ["domain_id"])

    op.create_table(
        "atlas_region",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("atlas_image_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column[str]("consent_status", consent_status, server_default="ACTIVE", nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("w", sa.Float(), nullable=False),
        sa.Column("h", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["atlas_image_id"],
            ["atlas_image.id"],
            name=op.f("fk_atlas_region_atlas_image_id_atlas_image"),
            ondelete="CASCADE",
        ),
        _user_fk("author_id", "atlas_region"),
        _user_fk("corrected_by", "atlas_region", "SET NULL"),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["visual_label.id"],
            name=op.f("fk_atlas_region_label_id_visual_label"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(BOX_CHECK, name=op.f("ck_atlas_region_box_in_image")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_atlas_region_position_non_negative")),
        sa.CheckConstraint(
            "consent_status <> 'REVOKED' OR description IS NULL",
            name=op.f("ck_atlas_region_revoked_is_empty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_atlas_region")),
        sa.UniqueConstraint(
            "atlas_image_id", "position", name=op.f("uq_atlas_region_atlas_image_id_position")
        ),
    )
    op.create_index(op.f("ix_atlas_region_author_id"), "atlas_region", ["author_id"])
    op.create_index(op.f("ix_atlas_region_label_id"), "atlas_region", ["label_id"])

    op.create_table(
        "atlas_review",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("atlas_image_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column[str]("decision", review_decision, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("image_version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["atlas_image_id"],
            ["atlas_image.id"],
            name=op.f("fk_atlas_review_atlas_image_id_atlas_image"),
        ),
        _user_fk("reviewer_id", "atlas_review"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_atlas_review")),
    )
    op.create_index(op.f("ix_atlas_review_atlas_image_id"), "atlas_review", ["atlas_image_id"])

    op.create_table(
        "atlas_submission",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("annotated_image_id", sa.Uuid(), nullable=False),
        sa.Column("atlas_image_id", sa.Uuid(), nullable=True),
        sa.Column[str]("status", atlas_submission_status, server_default="PENDING", nullable=False),
        sa.Column("error", sa.String(length=64), nullable=True),
        sa.Column(
            "pii_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_atlas_submission_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["annotated_image_id"],
            ["annotated_image.id"],
            name=op.f("fk_atlas_submission_annotated_image_id_annotated_image"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["atlas_image_id"],
            ["atlas_image.id"],
            name=op.f("fk_atlas_submission_atlas_image_id_atlas_image"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_atlas_submission")),
    )
    op.create_index(op.f("ix_atlas_submission_owner_id"), "atlas_submission", ["owner_id"])
    op.create_index(
        op.f("ix_atlas_submission_annotated_image_id"), "atlas_submission", ["annotated_image_id"]
    )
    op.execute("ALTER TABLE atlas_submission ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE atlas_submission FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY atlas_submission_owner_isolation ON atlas_submission
          USING      (owner_id = {CURRENT_USER})
          WITH CHECK (owner_id = {CURRENT_USER})
        """
    )


def downgrade() -> None:
    op.drop_table("atlas_submission")
    op.drop_table("atlas_review")
    op.drop_table("atlas_region")
    op.drop_table("atlas_image")
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
