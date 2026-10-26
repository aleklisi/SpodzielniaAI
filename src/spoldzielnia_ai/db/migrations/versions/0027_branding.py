"""Organisation branding: name, logo and colours (chunk 13.1, US-07).

Revision ID: 0027_branding
Revises: 0026_provider_removal
Create Date: 2026-10-26

``branding`` holds at most one row (``id = 1``). Its colours reach every page's CSS (13.4), so
the database refuses anything but ``#rrggbb`` there, whatever the service does (rule R2):

- ``brand_hex`` and ``accent_hex`` match ``^#[0-9a-f]{6}$``, and are set together with
  ``palette`` or not at all;
- every value in ``palette`` (``{"light": {token: hex}, "dark": {…}}``) is such a string;
- ``org_name`` is 1-60 characters without control characters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0027_branding"
down_revision: str | None = "0026_provider_removal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEX = "^#[0-9a-f]{6}$"
# Any value under the two modes that is not a #rrggbb string.
BAD_PALETTE_VALUE = '$.*.* ? (@.type() != "string" || !(@ like_regex "^#[0-9a-f]{6}$"))'


def upgrade() -> None:
    op.create_table(
        "branding",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("org_name", sa.String(60), nullable=True),
        sa.Column("brand_hex", sa.String(7), nullable=True),
        sa.Column("accent_hex", sa.String(7), nullable=True),
        sa.Column("palette", postgresql.JSONB(), nullable=True),
        sa.Column("logo_version", sa.Integer(), nullable=True),
        sa.Column("logo_sha256", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["app_user.id"],
            name=op.f("fk_branding_updated_by_app_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branding")),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint(f"brand_hex ~ '{HEX}'", name="brand_hex"),
        sa.CheckConstraint(f"accent_hex ~ '{HEX}'", name="accent_hex"),
        sa.CheckConstraint(
            "(brand_hex IS NULL) = (accent_hex IS NULL)"
            " AND (brand_hex IS NULL) = (palette IS NULL)",
            name="colours_together",
        ),
        sa.CheckConstraint(
            "palette IS NULL OR (jsonb_typeof(palette) = 'object'"
            " AND palette ?& array['light', 'dark']"
            f" AND NOT jsonb_path_exists(palette, '{BAD_PALETTE_VALUE}'))",
            name="palette_hex",
        ),
        sa.CheckConstraint(
            "char_length(org_name) BETWEEN 1 AND 60 AND org_name !~ '[[:cntrl:]]'",
            name="org_name",
        ),
        sa.CheckConstraint("(logo_version IS NULL) = (logo_sha256 IS NULL)", name="logo"),
    )


def downgrade() -> None:
    op.drop_table("branding")
