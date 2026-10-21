"""Visual atlas (Stage 9): domains and their label vocabulary.

Shared zone: labels are cooperative-wide knowledge, not private data. Admins manage domains,
experts curate each domain's labels, members only propose (9.1).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ARRAY, CheckConstraint, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import VisualDomainStatus, VisualLabelStatus


class VisualDomain(Entity):
    """A field of the cooperative's work (beekeeping, gardening…) with its own labels."""

    __tablename__ = "visual_domain"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[VisualDomainStatus] = mapped_column(
        server_default=VisualDomainStatus.ACTIVE.value, default=VisualDomainStatus.ACTIVE
    )
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))


class VisualLabel(Entity):
    """A label for a marked element. ``search_key`` is the accent-folded, lower-case name and
    synonyms (``atlas.text.search_key``), so "matka" finds "Matka pszczela" and "pszczoła"
    finds "pszczola"."""

    __tablename__ = "visual_label"
    __table_args__ = (
        UniqueConstraint("domain_id", "slug"),
        CheckConstraint("merged_into_id <> id", name="not_self_merged"),
        CheckConstraint(
            "(merged_into_id IS NULL) OR status = 'DEPRECATED'", name="merged_is_deprecated"
        ),
    )

    domain_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_domain.id", ondelete="RESTRICT"), index=True
    )
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(1000))
    synonyms: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), server_default=text("'{}'"), default=list
    )
    search_key: Mapped[str] = mapped_column(Text)
    status: Mapped[VisualLabelStatus] = mapped_column(
        server_default=VisualLabelStatus.ACTIVE.value, default=VisualLabelStatus.ACTIVE
    )
    merged_into_id: Mapped[UUID | None] = mapped_column(ForeignKey("visual_label.id"))
    proposed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
