"""Dataset exports of the visual atlas (chunk 9.7).

Revision ID: 0017_atlas_export
Revises: 0016_atlas
Create Date: 2026-10-21

``atlas_export`` (shared, admin-only through the API): one export of approved, consented,
export-allowed photos of one domain, built by worker-core into an encrypted archive. The
download token is stored only as a SHA-256 hash and works once. ``image_ids`` is the
manifest's photo list, so a later revoke shows up on the export.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0017_atlas_export"
down_revision: str | None = "0016_atlas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

atlas_export_status = postgresql.ENUM(
    "PENDING",
    "READY",
    "FAILED",
    "DOWNLOADED",
    "EXPIRED",
    name="atlas_export_status",
    create_type=False,
)
atlas_export_format = postgresql.ENUM("COCO", "YOLO", name="atlas_export_format", create_type=False)
ENUMS = (atlas_export_status, atlas_export_format)


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
        "atlas_export",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column[str]("status", atlas_export_status, server_default="PENDING", nullable=False),
        sa.Column[str]("format", atlas_export_format, nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("label_ids", postgresql.ARRAY(sa.Uuid()), nullable=True),
        sa.Column("min_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "image_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("image_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("region_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("object_key", sa.String(length=64), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name=op.f("fk_atlas_export_created_by_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["visual_domain.id"],
            name=op.f("fk_atlas_export_domain_id_visual_domain"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_atlas_export")),
        sa.UniqueConstraint("object_key", name=op.f("uq_atlas_export_object_key")),
    )
    op.create_index(op.f("ix_atlas_export_created_by"), "atlas_export", ["created_by"])


def downgrade() -> None:
    op.drop_table("atlas_export")
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
