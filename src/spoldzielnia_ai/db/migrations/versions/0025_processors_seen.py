"""Members see where their data is processed (chunk 10.8, P4).

Revision ID: 0025_processors_seen
Revises: 0024_image_embed
Create Date: 2026-10-23

``app_user.processors_seen_at``: when the member last acknowledged the list of model providers
cleared for private data. A cloud provider cleared after that shows the one-time banner
„Gdzie przetwarzane są Twoje dane” again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0025_processors_seen"
down_revision: str | None = "0024_image_embed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user", sa.Column("processors_seen_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_user", "processors_seen_at")
