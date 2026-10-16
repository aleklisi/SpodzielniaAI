from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import PromotionStatus


class Promotion(Entity):
    """A member's request to turn private notes into a wiki draft (3.5). Private (RLS), because
    it links the notes to the article. Source IDs and the title hint are encrypted under the
    row's DEK. ``injection_flags`` and ``pii_stats`` hold counts and kinds only."""

    __tablename__ = "promotion"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PromotionStatus] = mapped_column(
        server_default=PromotionStatus.PENDING.value, default=PromotionStatus.PENDING
    )
    article_id: Mapped[UUID] = mapped_column(unique=True)
    sources_ciphertext: Mapped[bytes]
    title_hint_ciphertext: Mapped[bytes | None]
    dek_wrapped: Mapped[bytes]
    source_ids_hash: Mapped[str] = mapped_column(String(64))
    source_count: Mapped[int] = mapped_column(Integer)
    prompt_name: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    prompt_digest: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    injection_flags: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )
    pii_stats: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )
    error: Mapped[str | None] = mapped_column(String(64))
