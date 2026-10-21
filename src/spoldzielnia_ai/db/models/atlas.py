"""Visual atlas (Stage 9).

- Shared zone: domains and their label vocabulary. Labels are cooperative-wide knowledge, not
  private data. Admins manage domains, experts curate each domain's labels, members propose (9.1).
- Private zone (RLS on ``owner_id``, like notes): a member's annotated photos (9.2).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import AnnotationSource, VisualDomainStatus, VisualLabelStatus


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


# Smallest box side: 0.5 % of the image side (9.2, A11). Mirrored by migration 0015's CHECK.
MIN_BOX_SIDE = 0.005


class AnnotatedImage(Entity):
    """A member's annotated photo: one per image attachment. The caption is ciphertext under
    the row's DEK (``table:row_id:owner_id``), and so are the descriptions of its marks.
    ``version`` is the optimistic-locking counter of the editor's full-replace saves."""

    __tablename__ = "annotated_image"
    __table_args__ = (CheckConstraint("version >= 1", name="version_positive"),)

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_note.id", ondelete="CASCADE"), index=True
    )
    attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("note_attachment.id", ondelete="CASCADE"), unique=True
    )
    domain_id: Mapped[UUID] = mapped_column(ForeignKey("visual_domain.id", ondelete="RESTRICT"))
    caption_ciphertext: Mapped[bytes | None]
    dek_wrapped: Mapped[bytes]
    version: Mapped[int] = mapped_column(server_default=text("1"), default=1)


class ImageAnnotation(Entity):
    """A rectangle on an annotated photo, normalised to the stored image (0..1)."""

    __tablename__ = "image_annotation"
    __table_args__ = (
        UniqueConstraint("annotated_image_id", "position"),
        CheckConstraint(
            f"x >= 0 AND y >= 0 AND w >= {MIN_BOX_SIDE} AND h >= {MIN_BOX_SIDE} "
            "AND x + w <= 1.000001 AND y + h <= 1.000001",
            name="box_in_image",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    annotated_image_id: Mapped[UUID] = mapped_column(
        ForeignKey("annotated_image.id", ondelete="CASCADE")
    )
    label_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_label.id", ondelete="RESTRICT"), index=True
    )
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    description_ciphertext: Mapped[bytes | None]
    source: Mapped[AnnotationSource] = mapped_column(
        server_default=AnnotationSource.USER.value, default=AnnotationSource.USER
    )
    position: Mapped[int]
