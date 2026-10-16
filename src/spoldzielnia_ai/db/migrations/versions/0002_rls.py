"""Row-Level Security on private-zone tables (gap G8/G9).

Revision ID: 0002_rls
Revises: 0001_core_schema
Create Date: 2026-10-16

``FORCE`` makes the policy bind the table owner too, so only a superuser bypasses it. The
policy fails closed: with ``app.current_user_id`` unset, ``NULLIF(...)`` is ``NULL``, so no row
matches and every insert fails the ``WITH CHECK``. Private-zone tables added later
(``note_attachment``, ``note_derivative`` in Stage 2) get the same policy in their own migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_rls"
down_revision: str | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_USER = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE private_note ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE private_note FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY private_note_owner_isolation ON private_note
          USING      (owner_id = {CURRENT_USER})
          WITH CHECK (owner_id = {CURRENT_USER})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY private_note_owner_isolation ON private_note")
    op.execute("ALTER TABLE private_note NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE private_note DISABLE ROW LEVEL SECURITY")
