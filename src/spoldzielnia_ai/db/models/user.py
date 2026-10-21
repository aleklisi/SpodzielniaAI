from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import Role, pg_enum


class User(Entity):
    """A member, identified by the IdP (issuer + subject), never by a local password.

    ``user`` is a reserved word in Postgres, hence ``app_user``. ``roles`` mirrors the IdP
    ``roles`` claim and is refreshed on login (1.5). An admin block (8.2) is kept apart from the
    shield's lock: the user is refused while either is set (``refused``).
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject"),
        CheckConstraint(
            "(blocked_at IS NULL) = (blocked_reason IS NULL)", name="ck_app_user_blocked_reason"
        ),
        Index("ix_app_user_username", func.lower(text("username"))),
    )

    oidc_issuer: Mapped[str] = mapped_column(Text)
    oidc_subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[Role]] = mapped_column(
        ARRAY(pg_enum(Role)), server_default=text("'{}'"), default=list
    )
    is_locked: Mapped[bool] = mapped_column(server_default=text("false"), default=False)
    risk_score: Mapped[float] = mapped_column(server_default=text("0"), default=0.0)
    # Set by the anti-distillation shield (5.2): a reason code, never query content.
    locked_reason: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None]
    # Stage 8: user management.
    username: Mapped[str | None] = mapped_column(Text)
    blocked_at: Mapped[datetime | None]
    blocked_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", name="fk_app_user_blocked_by_app_user", ondelete="SET NULL")
    )
    blocked_reason: Mapped[str | None] = mapped_column(String(64))
    blocked_note: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", name="fk_app_user_created_by_app_user", ondelete="SET NULL")
    )
    last_login_at: Mapped[datetime | None]
    required_actions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'"), default=list
    )

    @property
    def refused(self) -> bool:
        """Locked by the shield or blocked by an admin."""
        return self.is_locked or self.blocked_at is not None
