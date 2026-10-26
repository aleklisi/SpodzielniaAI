from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Base


class Branding(Base):
    """The organisation's name, logo and colours (13.1, US-07). One row (``id = 1``) or none:
    no row, and ``NULL`` columns, mean the default look. ``palette`` holds the derived tokens
    (``branding.palette``), only ``#rrggbb`` values, which migration 0027 also checks.
    ``version`` grows on every change (cache keys); the logo's files are stored under
    ``logo_version``, the version that uploaded them."""

    __tablename__ = "branding"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    org_name: Mapped[str | None] = mapped_column(String(60))
    brand_hex: Mapped[str | None] = mapped_column(String(7))
    accent_hex: Mapped[str | None] = mapped_column(String(7))
    # none_as_null: no palette is SQL NULL (the colours CHECK of 0027), not JSON `null`.
    palette: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    logo_version: Mapped[int | None]
    logo_sha256: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(server_default="0")
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
