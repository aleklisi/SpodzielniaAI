"""Creator consent: revoked blocks become content-free tombstones (chunk 4.3).

Revision ID: 0008_consent
Revises: 0007_governance
Create Date: 2026-10-19

- ``article_block.content`` / ``content_hash`` become nullable. A REVOKED block keeps its row
  (id, position, author, status) for audit integrity, but no text and no hash of it.
- ``ck_article_block_revoked_is_empty``: REVOKED ⇔ no content. The database refuses a revoked
  block that still holds text, and an active or suspended block without any.
- ``article_block_revoke_is_final``: a REVOKED block can never change status again, for any
  role. Revocation can't be undone by an update, only by new content in a new block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0008_consent"
down_revision: str | None = "0007_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVOKE_IS_FINAL = """
CREATE FUNCTION article_block_revoke_is_final() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.consent_status = 'REVOKED' AND NEW.consent_status <> 'REVOKED' THEN
    RAISE EXCEPTION 'article_block %: revoked consent is final', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.alter_column("article_block", "content", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "article_block", "content_hash", existing_type=sa.String(length=64), nullable=True
    )
    op.create_check_constraint(
        op.f("ck_article_block_revoked_is_empty"),
        "article_block",
        "(consent_status = 'REVOKED') = (content IS NULL AND content_hash IS NULL)",
    )
    op.execute(REVOKE_IS_FINAL)
    op.execute(
        """
        CREATE TRIGGER article_block_revoke_is_final BEFORE UPDATE ON article_block
          FOR EACH ROW EXECUTE FUNCTION article_block_revoke_is_final()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER article_block_revoke_is_final ON article_block")
    op.execute("DROP FUNCTION article_block_revoke_is_final()")
    op.drop_constraint(op.f("ck_article_block_revoked_is_empty"), "article_block", type_="check")
    # Tombstones can't regain their text: they are removed on the way down.
    op.execute("DELETE FROM article_block WHERE content IS NULL")
    op.alter_column(
        "article_block", "content_hash", existing_type=sa.String(length=64), nullable=False
    )
    op.alter_column("article_block", "content", existing_type=sa.Text(), nullable=False)
