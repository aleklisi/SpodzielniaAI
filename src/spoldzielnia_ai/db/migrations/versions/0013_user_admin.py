"""Admin user management (chunk 8.2).

Revision ID: 0013_user_admin
Revises: 0012_llm_config
Create Date: 2026-10-21

Keycloak stays the credential store; ``app_user`` mirrors what the admin console lists and
what authorization reads on every request.

- ``username``: Keycloak's ``preferred_username`` (search, display).
- ``blocked_*``: an admin block, kept apart from the shield's ``is_locked``/``locked_*`` so
  neither overwrites the other. A user is refused when either is set.
- ``created_by``: the admin who created the account (NULL for users who arrived by login).
- ``last_login_at``, ``required_actions``: mirrored from logins and Keycloak (list status).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

revision: str = "0013_user_admin"
down_revision: str | None = "0012_llm_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _user_fk(column: str) -> sa.Column[uuid.UUID]:
    return sa.Column(
        column,
        sa.Uuid(),
        sa.ForeignKey("app_user.id", name=f"fk_app_user_{column}_app_user", ondelete="SET NULL"),
        nullable=True,
    )


def upgrade() -> None:
    op.add_column("app_user", sa.Column("username", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_user", _user_fk("blocked_by"))
    op.add_column("app_user", sa.Column("blocked_reason", sa.String(64), nullable=True))
    op.add_column("app_user", sa.Column("blocked_note", sa.String(200), nullable=True))
    op.add_column("app_user", _user_fk("created_by"))
    op.add_column("app_user", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "app_user",
        sa.Column(
            "required_actions",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_app_user_blocked_reason", "app_user", "(blocked_at IS NULL) = (blocked_reason IS NULL)"
    )
    op.create_index("ix_app_user_username", "app_user", [sa.text("lower(username)")])


def downgrade() -> None:
    op.drop_index("ix_app_user_username", table_name="app_user")
    op.drop_constraint("ck_app_user_blocked_reason", "app_user", type_="check")
    for column in (
        "required_actions",
        "last_login_at",
        "created_by",
        "blocked_note",
        "blocked_reason",
        "blocked_by",
        "blocked_at",
        "username",
    ):
        op.drop_column("app_user", column)
