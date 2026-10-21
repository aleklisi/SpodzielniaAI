"""Domain enums stored as Postgres enum types.

The database stores the enum *values* (``values_callable``), so renaming a Python member never
silently changes what is written. Each enum has a fixed Postgres type name.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class Role(StrEnum):
    MEMBER = "MEMBER"
    EXPERT = "EXPERT"
    ADMIN = "ADMIN"


class ConsentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class ArticleStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    CANONICAL = "CANONICAL"
    SUPERSEDED = "SUPERSEDED"
    DISPUTED = "DISPUTED"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    REJECTED = "REJECTED"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"
    MARK_DISPUTED = "MARK_DISPUTED"


class ScanStatus(StrEnum):
    PENDING = "PENDING"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    REJECTED = "REJECTED"


class DerivativeKind(StrEnum):
    TRANSCRIPT = "TRANSCRIPT"
    IMAGE_DESCRIPTION = "IMAGE_DESCRIPTION"
    PARSED_DOC = "PARSED_DOC"
    FETCHED_LINK = "FETCHED_LINK"
    ANNOTATIONS = "ANNOTATIONS"  # 9.2: a photo's caption and marks as text (written by its owner)


class DerivativeStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"  # 3.3: injection score above threshold


class PromotionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class VectorOpKind(StrEnum):
    UPSERT = "UPSERT"
    SET_PAYLOAD = "SET_PAYLOAD"
    DELETE = "DELETE"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"


class LlmProviderKind(StrEnum):
    """Derived from the endpoint allowlist the base URL matched (7.1), never chosen freely."""

    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class LlmCapability(StrEnum):
    CHAT = "CHAT"
    VLM = "VLM"
    EMBED = "EMBED"


class LlmRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class LlmRoutePurpose(StrEnum):
    """Purposes an admin can assign a model to (``llm.gateway.LLMPurpose`` minus the playground)."""

    PII_SCRUB = "PII_SCRUB"
    PRIVATE_NOTE = "PRIVATE_NOTE"
    PROMOTION = "PROMOTION"
    VLM = "VLM"
    EMBED = "EMBED"
    RAG = "RAG"


class VisualDomainStatus(StrEnum):
    """A domain of the visual atlas (9.1). ARCHIVED: read-only, accepts no new photos."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class VisualLabelStatus(StrEnum):
    """PROPOSED by a member until an expert approves (ACTIVE), rejects or merges it. A merged
    label is DEPRECATED with ``merged_into_id`` set."""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    REJECTED = "REJECTED"


class AnnotationSource(StrEnum):
    """Who drew a mark: the member, or a recognition suggestion they accepted (9.9)."""

    USER = "USER"
    SUGGESTED = "SUGGESTED"


PG_ENUM_NAMES: dict[type[StrEnum], str] = {
    Role: "user_role",
    ConsentStatus: "consent_status",
    ArticleStatus: "article_status",
    ReviewDecision: "review_decision",
    ScanStatus: "scan_status",
    DerivativeKind: "derivative_kind",
    DerivativeStatus: "derivative_status",
    PromotionStatus: "promotion_status",
    VectorOpKind: "vector_op_kind",
    OutboxStatus: "outbox_status",
    LlmProviderKind: "llm_provider_kind",
    LlmCapability: "llm_capability",
    LlmRevisionStatus: "llm_revision_status",
    LlmRoutePurpose: "llm_route_purpose",
    VisualDomainStatus: "visual_domain_status",
    VisualLabelStatus: "visual_label_status",
    AnnotationSource: "annotation_source",
}


def _values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def pg_enum(enum_cls: type[StrEnum]) -> SAEnum:
    """SQLAlchemy type for ``enum_cls`` bound to its Postgres enum type."""
    return SAEnum(
        enum_cls,
        name=PG_ENUM_NAMES[enum_cls],
        values_callable=_values,
        validate_strings=True,
    )
