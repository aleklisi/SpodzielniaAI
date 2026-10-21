"""Visual atlas (Stage 9).

- Shared zone: domains and their label vocabulary. Labels are cooperative-wide knowledge, not
  private data. Admins manage domains, experts curate each domain's labels, members propose (9.1).
- Private zone (RLS on ``owner_id``, like notes): a member's annotated photos (9.2) and their
  submissions to the atlas (9.4).
- Shared zone: atlas photos and their marks, with block-level provenance (``author_id`` +
  ``consent_status``, AGENTS.md §3.2), and the experts' reviews (9.4/9.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import (
    AnnotationSource,
    AtlasExportFormat,
    AtlasExportStatus,
    AtlasImageStatus,
    AtlasSubmissionStatus,
    ConsentStatus,
    ReviewDecision,
    VisualDomainStatus,
    VisualLabelStatus,
)


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


_BOX_CHECK = (
    f"x >= 0 AND y >= 0 AND w >= {MIN_BOX_SIDE} AND h >= {MIN_BOX_SIDE} "
    "AND x + w <= 1.000001 AND y + h <= 1.000001"
)


class AtlasSubmission(Entity):
    """Private (RLS): which annotated photo an atlas photo was made from, and the preparation
    state. ``pii_stats`` holds entity counts only."""

    __tablename__ = "atlas_submission"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    annotated_image_id: Mapped[UUID] = mapped_column(
        ForeignKey("annotated_image.id", ondelete="CASCADE"), index=True
    )
    atlas_image_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("atlas_image.id", ondelete="SET NULL")
    )
    status: Mapped[AtlasSubmissionStatus] = mapped_column(
        server_default=AtlasSubmissionStatus.PENDING.value, default=AtlasSubmissionStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(String(64))
    pii_stats: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )


class AtlasImage(Entity):
    """A photo in the shared atlas: a metadata-free copy of a member's photo, encrypted under
    the row's DEK (``atlas_image:id:author_id``). Caption and region texts are PII-scrubbed.
    A REVOKED photo is a tombstone: no objects and no text (CHECK, migration 0016)."""

    __tablename__ = "atlas_image"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(consent_status = 'REVOKED') = (object_key IS NULL AND caption IS NULL "
            "AND preview_object_key IS NULL AND thumb_object_key IS NULL)",
            name="revoked_is_empty",
        ),
    )

    author_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    consent_status: Mapped[ConsentStatus] = mapped_column(
        server_default=ConsentStatus.ACTIVE.value, default=ConsentStatus.ACTIVE
    )
    status: Mapped[AtlasImageStatus] = mapped_column(
        server_default=AtlasImageStatus.DRAFT.value, default=AtlasImageStatus.DRAFT, index=True
    )
    domain_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_domain.id", ondelete="RESTRICT"), index=True
    )
    object_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    preview_object_key: Mapped[str | None] = mapped_column(String(64))
    thumb_object_key: Mapped[str | None] = mapped_column(String(64))
    dek_wrapped: Mapped[bytes]
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    phash: Mapped[str | None] = mapped_column(String(16))  # 64-bit dHash, hex
    caption: Mapped[str | None] = mapped_column(Text)
    # Kinds and scores only (people, faces, documents…), never descriptions.
    privacy_flags: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )
    allow_export: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    show_author: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    version: Mapped[int] = mapped_column(server_default=text("1"), default=1)
    submitted_at: Mapped[datetime | None]
    verified_at: Mapped[datetime | None]
    status_changed_at: Mapped[datetime] = mapped_column(server_default=func.now())
    review_alert: Mapped[str | None] = mapped_column(Text)


class AtlasRegion(Entity):
    """A mark on an atlas photo with its own provenance. ``corrected_by``: the expert who
    changed a member's mark (the member stays its author)."""

    __tablename__ = "atlas_region"
    __table_args__ = (
        UniqueConstraint("atlas_image_id", "position"),
        CheckConstraint(_BOX_CHECK, name="box_in_image"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(
            "consent_status <> 'REVOKED' OR description IS NULL", name="revoked_is_empty"
        ),
    )

    atlas_image_id: Mapped[UUID] = mapped_column(ForeignKey("atlas_image.id", ondelete="CASCADE"))
    author_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    consent_status: Mapped[ConsentStatus] = mapped_column(
        server_default=ConsentStatus.ACTIVE.value, default=ConsentStatus.ACTIVE
    )
    label_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_label.id", ondelete="RESTRICT"), index=True
    )
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    position: Mapped[int]


class AtlasReview(Entity):
    """An expert's decision on an atlas photo (9.5). A governance record, never cascaded."""

    __tablename__ = "atlas_review"

    atlas_image_id: Mapped[UUID] = mapped_column(ForeignKey("atlas_image.id"), index=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"))
    decision: Mapped[ReviewDecision]
    comment: Mapped[str | None] = mapped_column(Text)
    image_version: Mapped[int]


class AtlasExport(Entity):
    """A dataset export (9.7). The archive is encrypted under the row's DEK; the download
    token is kept only as its SHA-256 and works once. ``image_ids``: the photos in the manifest
    (a later revoke is visible on the export)."""

    __tablename__ = "atlas_export"

    created_by: Mapped[UUID] = mapped_column(ForeignKey("app_user.id"), index=True)
    status: Mapped[AtlasExportStatus] = mapped_column(
        server_default=AtlasExportStatus.PENDING.value, default=AtlasExportStatus.PENDING
    )
    format: Mapped[AtlasExportFormat]
    domain_id: Mapped[UUID] = mapped_column(ForeignKey("visual_domain.id", ondelete="RESTRICT"))
    label_ids: Mapped[list[UUID] | None] = mapped_column(ARRAY(Uuid()))
    min_verified_at: Mapped[datetime | None]
    image_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid()), server_default=text("'{}'"), default=list
    )
    image_count: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    region_count: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    size: Mapped[int | None] = mapped_column(BigInteger)
    dek_wrapped: Mapped[bytes]
    token_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    downloaded_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(String(64))
