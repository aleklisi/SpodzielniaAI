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


PG_ENUM_NAMES: dict[type[StrEnum], str] = {
    Role: "user_role",
    ConsentStatus: "consent_status",
    ArticleStatus: "article_status",
    ReviewDecision: "review_decision",
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
