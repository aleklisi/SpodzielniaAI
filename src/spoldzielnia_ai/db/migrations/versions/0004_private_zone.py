"""Private zone: attachments and derivatives with RLS; encrypted note titles.

Revision ID: 0004_private_zone
Revises: 0003_audit
Create Date: 2026-10-16

- ``note_attachment`` and ``note_derivative`` get the owner policy of ``private_note`` (gap G9),
  with ``FORCE ROW LEVEL SECURITY``.
- ``private_note.title`` (plaintext) becomes ``title_ciphertext``, and ``body_nonce`` is dropped:
  field blobs carry their own nonce (``core.crypto``). No stored notes exist before this
  revision. On downgrade, titles come back empty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004_private_zone"
down_revision: str | None = "0003_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
PRIVATE_TABLES = ("note_attachment", "note_derivative")

scan_status = postgresql.ENUM(
    "PENDING", "CLEAN", "INFECTED", "REJECTED", name="scan_status", create_type=False
)
derivative_kind = postgresql.ENUM(
    "TRANSCRIPT",
    "IMAGE_DESCRIPTION",
    "PARSED_DOC",
    "FETCHED_LINK",
    name="derivative_kind",
    create_type=False,
)
derivative_status = postgresql.ENUM(
    "PENDING", "PROCESSING", "DONE", "FAILED", name="derivative_status", create_type=False
)
ENUMS = (scan_status, derivative_kind, derivative_status)


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
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)

    op.drop_column("private_note", "title")
    op.drop_column("private_note", "body_nonce")
    op.add_column("private_note", sa.Column("title_ciphertext", sa.LargeBinary(), nullable=False))

    op.create_table(
        "note_attachment",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column[str]("scan_status", scan_status, server_default="PENDING", nullable=False),
        sa.Column("keep_location", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("filename_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_note_attachment_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["private_note.id"],
            name=op.f("fk_note_attachment_note_id_private_note"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_attachment")),
        sa.UniqueConstraint("object_key", name=op.f("uq_note_attachment_object_key")),
    )
    op.create_index(op.f("ix_note_attachment_owner_id"), "note_attachment", ["owner_id"])
    op.create_index(op.f("ix_note_attachment_note_id"), "note_attachment", ["note_id"])

    op.create_table(
        "note_derivative",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=True),
        sa.Column[str]("kind", derivative_kind, nullable=False),
        sa.Column[str]("status", derivative_status, server_default="PENDING", nullable=False),
        sa.Column("source_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=False),
        sa.Column("error", sa.String(length=64), nullable=True),
        sa.Column(
            "injection_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_entity_columns(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("fk_note_derivative_owner_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["private_note.id"],
            name=op.f("fk_note_derivative_note_id_private_note"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["note_attachment.id"],
            name=op.f("fk_note_derivative_attachment_id_note_attachment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_note_derivative")),
    )
    op.create_index(op.f("ix_note_derivative_owner_id"), "note_derivative", ["owner_id"])
    op.create_index(op.f("ix_note_derivative_note_id"), "note_derivative", ["note_id"])
    op.create_index(op.f("ix_note_derivative_attachment_id"), "note_derivative", ["attachment_id"])

    for table in PRIVATE_TABLES:
        _owner_policy(table)


def downgrade() -> None:
    op.drop_table("note_derivative")  # policies go with the table
    op.drop_table("note_attachment")
    op.drop_column("private_note", "title_ciphertext")
    op.add_column(
        "private_note",
        sa.Column(
            "body_nonce", sa.LargeBinary(), server_default=sa.text("''::bytea"), nullable=False
        ),
    )
    op.add_column(
        "private_note",
        sa.Column("title", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
    op.alter_column("private_note", "body_nonce", server_default=None)
    op.alter_column("private_note", "title", server_default=None)
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
