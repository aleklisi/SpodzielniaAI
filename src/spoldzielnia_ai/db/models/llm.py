from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Base, Entity
from spoldzielnia_ai.db.enums import (
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
)


class LlmProvider(Entity):
    """An OpenAI-compatible endpoint managed by an admin (7.1). Global configuration, not user
    data (no RLS). ``kind`` is derived from the endpoint allowlists in settings and re-checked at
    runtime; it decides the network path only. The API key is envelope-encrypted under the row's
    DEK (AAD ``llm_provider:<id>``); ``inherit_env_key`` uses the Docker secret instead
    (providers imported from ``.env``).

    ``private_data_cleared`` (10.1) is what lets private purposes use the provider: set by an
    ADMIN with step-up, together with the region, retention and contract reference shown to
    members. It counts only while the endpoint is on ``llm.private_data_endpoints``."""

    __tablename__ = "llm_provider"
    # Timestamps and identity values come back via RETURNING: the admin API returns the rows
    # after their session has closed.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (
        CheckConstraint(
            "NOT private_data_cleared OR (cleared_region IS NOT NULL AND cleared_retention IS NOT"
            " NULL AND cleared_contract_ref IS NOT NULL AND cleared_at IS NOT NULL)",
            name="clearance_complete",
        ),
    )

    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[LlmProviderKind]
    base_url: Mapped[str] = mapped_column(String(512))
    litellm_provider: Mapped[str] = mapped_column(String(32), server_default="openai")
    api_key_wrapped: Mapped[bytes | None]
    api_key_ciphertext: Mapped[bytes | None]
    api_key_hint: Mapped[str | None] = mapped_column(String(8))
    inherit_env_key: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    structured_output: Mapped[str | None] = mapped_column(String(16))
    timeout_seconds: Mapped[float | None] = mapped_column(Float)
    disabled_at: Mapped[datetime | None]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    private_data_cleared: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    cleared_region: Mapped[str | None] = mapped_column(String(64))
    cleared_retention: Mapped[str | None] = mapped_column(String(64))
    cleared_contract_ref: Mapped[str | None] = mapped_column(String(200))
    cleared_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    cleared_at: Mapped[datetime | None]


class LlmModel(Entity):
    """A model on a provider, with its generation parameters and the last probe (7.4). The
    probe report holds timings, token counts and pass/fail only: never prompts or output."""

    __tablename__ = "llm_model"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (UniqueConstraint("provider_id", "model_name", "capability"),)

    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_provider.id", ondelete="RESTRICT"), index=True
    )
    model_name: Mapped[str] = mapped_column(String(256))
    capability: Mapped[LlmCapability]
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    embed_dimension: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(String(500))
    disabled_at: Mapped[datetime | None]
    last_probe_at: Mapped[datetime | None]
    last_probe_ok: Mapped[bool | None]
    last_probe_report: Mapped[dict[str, Any] | None]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))


class LlmConfigRevision(Entity):
    """A numbered configuration. Exactly one is ACTIVE. Activation freezes ``snapshot`` (the
    resolved providers, models and routes, without secrets); the runtime reads only the
    snapshot, so later catalog edits change nothing until the next activation. ACTIVE and
    ARCHIVED rows are immutable (trigger, migration 0012)."""

    __tablename__ = "llm_config_revision"
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 - SQLAlchemy reads it
    __table_args__ = (
        Index(
            "uq_llm_config_revision_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    number: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    status: Mapped[LlmRevisionStatus] = mapped_column(
        server_default=LlmRevisionStatus.DRAFT.value, default=LlmRevisionStatus.DRAFT
    )
    comment: Mapped[str | None] = mapped_column(String(500))
    snapshot: Mapped[dict[str, Any] | None]
    based_on: Mapped[int | None] = mapped_column(BigInteger)
    rolled_back_from: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    activated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None]


class LlmRouting(Base):
    """Purpose → model in a DRAFT revision. A trigger rejects providers not cleared for private
    data on private purposes (10.1), capability mismatches, and any change once the revision
    left DRAFT."""

    __tablename__ = "llm_routing"

    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_config_revision.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[LlmRoutePurpose] = mapped_column(primary_key=True)
    model_id: Mapped[UUID] = mapped_column(
        ForeignKey("llm_model.id", ondelete="RESTRICT"), index=True
    )
