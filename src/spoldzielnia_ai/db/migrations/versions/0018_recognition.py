"""Recognition results on notebook photos (chunk 9.9).

Revision ID: 0018_recognition
Revises: 0017_atlas_export
Create Date: 2026-10-21

``derivative_kind`` gains ``RECOGNITION``: the answer of „Rozpoznaj” for a photo (suggested
marks and the atlas photos it relied on), encrypted like every derivative, private to its owner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0018_recognition"
down_revision: str | None = "0017_atlas_export"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE derivative_kind ADD VALUE IF NOT EXISTS 'RECOGNITION'")


def downgrade() -> None:
    # Postgres cannot drop an enum value: rebuild derivative_kind without RECOGNITION.
    op.execute("ALTER TABLE note_derivative NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM note_derivative WHERE kind = 'RECOGNITION'")
    op.execute("ALTER TABLE note_derivative FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE note_derivative ALTER COLUMN kind TYPE text")
    op.execute("DROP TYPE derivative_kind")
    op.execute(
        "CREATE TYPE derivative_kind AS ENUM "
        "('TRANSCRIPT', 'IMAGE_DESCRIPTION', 'PARSED_DOC', 'FETCHED_LINK', 'ANNOTATIONS')"
    )
    op.execute(
        "ALTER TABLE note_derivative ALTER COLUMN kind TYPE derivative_kind "
        "USING kind::derivative_kind"
    )
