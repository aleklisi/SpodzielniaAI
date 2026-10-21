"""Private photo annotations (chunk 9.2).

Revision ID: 0015_image_annotation
Revises: 0014_visual_labels
Create Date: 2026-10-21

Private zone (RLS owner policy, FORCE), like notes:

- ``annotated_image``: one per image attachment; the photo's domain, its caption (ciphertext
  under the row's DEK) and an optimistic-locking ``version``.
- ``image_annotation``: a rectangle in coordinates normalised to the stored (EXIF-rotated)
  image, its label and description (ciphertext under the image's DEK).
- ``note_attachment``: ``width``/``height`` of the stored image and ``preview_object_key``
  (an encrypted JPEG rendition of at most 1600 px, generated on upload or on first view).
- ``derivative_kind`` gains ``ANNOTATIONS``: the caption and marks as text, so "ask my
  notebook" finds annotated photos.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0015_image_annotation"
down_revision: str | None = "0014_visual_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

annotation_source = postgresql.ENUM(
    "USER", "SUGGESTED", name="annotation_source", create_type=False
)
ENUMS = (annotation_source,)

# Smallest side of a box: 0.5 % of the image side (A11).
MIN_SIDE = 0.005
BOX_CHECK = (
    f"x >= 0 AND y >= 0 AND w >= {MIN_SIDE} AND h >= {MIN_SIDE} "
    "AND x + w <= 1.000001 AND y + h <= 1.000001"
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


def _owner_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_owner_isolation ON {table}
          USING      (owner_id = {CURRENT_USER})
          WITH CHECK (owner_id = {CURRENT_USER})
        """
    )


def upgrade() -> None:
    op.execute("ALTER TYPE derivative_kind ADD VALUE IF NOT EXISTS 'ANNOTATIONS'")
    for enum in ENUMS:
        enum.create(op.get_bind())

    op.add_column("note_attachment", sa.Column("width", sa.Integer(), nullable=True))
    op.add_column("note_attachment", sa.Column("height", sa.Integer(), nullable=True))
    op.add_column(
        "note_attachment", sa.Column("preview_object_key", sa.String(length=64), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_note_attachment_preview_object_key"), "note_attachment", ["preview_object_key"]
    )

    op.create_table(
        "annotated_image",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("caption_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_annotated_image_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["private_note.id"],
            name=op.f("fk_annotated_image_note_id_private_note"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["note_attachment.id"],
            name=op.f("fk_annotated_image_attachment_id_note_attachment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["visual_domain.id"],
            name=op.f("fk_annotated_image_domain_id_visual_domain"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_annotated_image_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annotated_image")),
        sa.UniqueConstraint("attachment_id", name=op.f("uq_annotated_image_attachment_id")),
    )
    op.create_index(op.f("ix_annotated_image_owner_id"), "annotated_image", ["owner_id"])
    op.create_index(op.f("ix_annotated_image_note_id"), "annotated_image", ["note_id"])
    _owner_policy("annotated_image")

    op.create_table(
        "image_annotation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("annotated_image_id", sa.Uuid(), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("w", sa.Float(), nullable=False),
        sa.Column("h", sa.Float(), nullable=False),
        sa.Column("description_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column[str]("source", annotation_source, server_default="USER", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_image_annotation_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["annotated_image_id"],
            ["annotated_image.id"],
            name=op.f("fk_image_annotation_annotated_image_id_annotated_image"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["visual_label.id"],
            name=op.f("fk_image_annotation_label_id_visual_label"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(BOX_CHECK, name=op.f("ck_image_annotation_box_in_image")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_image_annotation_position_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_image_annotation")),
        sa.UniqueConstraint(
            "annotated_image_id",
            "position",
            name=op.f("uq_image_annotation_annotated_image_id_position"),
        ),
    )
    op.create_index(op.f("ix_image_annotation_owner_id"), "image_annotation", ["owner_id"])
    op.create_index(op.f("ix_image_annotation_label_id"), "image_annotation", ["label_id"])
    _owner_policy("image_annotation")


def downgrade() -> None:
    op.drop_table("image_annotation")
    op.drop_table("annotated_image")
    op.drop_constraint(
        op.f("uq_note_attachment_preview_object_key"), "note_attachment", type_="unique"
    )
    for column in ("preview_object_key", "height", "width"):
        op.drop_column("note_attachment", column)
    for enum in reversed(ENUMS):
        enum.drop(op.get_bind())
    # Postgres cannot drop an enum value: rebuild derivative_kind without ANNOTATIONS.
    op.execute("ALTER TABLE note_derivative NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM note_derivative WHERE kind = 'ANNOTATIONS'")
    op.execute("ALTER TABLE note_derivative FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE note_derivative ALTER COLUMN kind TYPE text")
    op.execute("DROP TYPE derivative_kind")
    op.execute(
        "CREATE TYPE derivative_kind AS ENUM "
        "('TRANSCRIPT', 'IMAGE_DESCRIPTION', 'PARSED_DOC', 'FETCHED_LINK')"
    )
    op.execute(
        "ALTER TABLE note_derivative ALTER COLUMN kind TYPE derivative_kind "
        "USING kind::derivative_kind"
    )
