from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import Role, pg_enum


class User(Entity):
    """A member, identified by the IdP (issuer + subject), never by a local password.

    ``user`` is a reserved word in Postgres, hence ``app_user``. ``roles`` mirrors the IdP
    ``roles`` claim and is refreshed on login (1.5).
    """

    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("oidc_issuer", "oidc_subject"),)

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
