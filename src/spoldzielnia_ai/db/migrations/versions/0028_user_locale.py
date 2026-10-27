"""The UI language on the account (chunk 14.1, US-08).

Revision ID: 0028_user_locale
Revises: 0027_branding
Create Date: 2026-10-27

- ``app_user.locale``: ``pl`` or ``en``; NULL means "not chosen" and reads as Polish.
- ``wiki_article.review_alert`` held the Polish sentence the consent flow wrote. It now holds
  the code ``revision_required``, which the frontend renders in the reader's language.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0028_user_locale"
down_revision: str | None = "0027_branding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVISION_ALERT_PL = "Wymagana rewizja – wycofano część wkładu autorskiego"  # noqa: RUF001
REVISION_REQUIRED = "revision_required"


def upgrade() -> None:
    op.add_column("app_user", sa.Column("locale", sa.String(2), nullable=True))
    op.create_check_constraint(op.f("ck_app_user_locale"), "app_user", "locale IN ('pl', 'en')")
    op.execute(
        sa.text(
            "UPDATE wiki_article SET review_alert = :code WHERE review_alert = :text"
        ).bindparams(code=REVISION_REQUIRED, text=REVISION_ALERT_PL)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE wiki_article SET review_alert = :text WHERE review_alert = :code"
        ).bindparams(code=REVISION_REQUIRED, text=REVISION_ALERT_PL)
    )
    op.drop_constraint(op.f("ck_app_user_locale"), "app_user", type_="check")
    op.drop_column("app_user", "locale")
