"""Admin-configurable LLM providers, models and revisions (chunk 7.1).

Revision ID: 0012_llm_config
Revises: 0011_hidden_articles
Create Date: 2026-10-20

Global configuration, not user data: no RLS. Triggers are the database layer of the local-only
rule (AGENTS.md §3.4); the service and the gateway check it too.

- ``llm_routing``: purposes in ``LOCAL_ONLY`` may only point at a model of a LOCAL provider, the
  model's capability must fit the purpose, and rows change only while their revision is DRAFT.
- ``llm_config_revision``: ACTIVE and ARCHIVED rows are immutable except ACTIVE → ARCHIVED; a
  row becomes ACTIVE only with a snapshot whose local-only routes all use LOCAL providers.
- ``llm_provider``: cannot turn CLOUD while a local-only route (draft or active) uses it.

``LOCAL_ONLY`` repeats ``llm.gateway.ALWAYS_LOCAL`` (migrations are frozen); a unit test checks
that the two agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0012_llm_config"
down_revision: str | None = "0011_hidden_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_ONLY = ("PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "VLM", "EMBED")
_LOCAL_ONLY_ARRAY = "ARRAY[" + ", ".join(f"'{p}'" for p in LOCAL_ONLY) + "]"
_LOCAL_ONLY_SQL = _LOCAL_ONLY_ARRAY + "::llm_route_purpose[]"
_LOCAL_ONLY_TEXT = _LOCAL_ONLY_ARRAY + "::text[]"

provider_kind = postgresql.ENUM("LOCAL", "CLOUD", name="llm_provider_kind", create_type=False)
capability = postgresql.ENUM("CHAT", "VLM", "EMBED", name="llm_capability", create_type=False)
revision_status = postgresql.ENUM(
    "DRAFT", "ACTIVE", "ARCHIVED", name="llm_revision_status", create_type=False
)
route_purpose = postgresql.ENUM(
    "PII_SCRUB",
    "PRIVATE_NOTE",
    "PROMOTION",
    "VLM",
    "EMBED",
    "RAG",
    name="llm_route_purpose",
    create_type=False,
)
ENUMS = (provider_kind, capability, revision_status, route_purpose)

ROUTING_FUNCTION = f"""
CREATE FUNCTION llm_routing_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  rev_status llm_revision_status;
  model_capability llm_capability;
  model_kind llm_provider_kind;
BEGIN
  SELECT status INTO rev_status FROM llm_config_revision
    WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.revision_id ELSE NEW.revision_id END;
  -- Missing: the revision itself is being deleted (cascade from a DRAFT).
  IF rev_status IS NOT NULL AND rev_status <> 'DRAFT' THEN
    RAISE EXCEPTION 'llm_routing of a % revision is immutable', rev_status
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.revision_id <> OLD.revision_id THEN
    RAISE EXCEPTION 'llm_routing cannot move between revisions'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  SELECT m.capability, p.kind INTO model_capability, model_kind
    FROM llm_model m JOIN llm_provider p ON p.id = m.provider_id
    WHERE m.id = NEW.model_id;
  IF NEW.purpose = ANY({_LOCAL_ONLY_SQL}) AND model_kind IS DISTINCT FROM 'LOCAL' THEN
    RAISE EXCEPTION 'purpose % may only use a LOCAL provider', NEW.purpose
      USING ERRCODE = 'check_violation';
  END IF;
  IF model_capability IS DISTINCT FROM (CASE NEW.purpose
      WHEN 'VLM' THEN 'VLM' WHEN 'EMBED' THEN 'EMBED' ELSE 'CHAT' END)::llm_capability THEN
    RAISE EXCEPTION 'purpose % cannot use a % model', NEW.purpose, model_capability
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""  # noqa: S608 # nosec B608 - constant text, no input

REVISION_FUNCTION = f"""
CREATE FUNCTION llm_revision_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  purpose text;
  deployment text;
  provider uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'DRAFT' THEN
      RAISE EXCEPTION 'a % llm_config_revision cannot be deleted', OLD.status
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status <> 'DRAFT' THEN
    IF NOT (OLD.status = 'ACTIVE' AND NEW.status = 'ARCHIVED')
       OR (to_jsonb(NEW) - 'status' - 'updated_at')
          IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at') THEN
      RAISE EXCEPTION 'a % llm_config_revision is immutable', OLD.status
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status = 'ARCHIVED' AND TG_OP = 'INSERT' THEN
    RAISE EXCEPTION 'llm_config_revision cannot be created ARCHIVED'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.status = 'ACTIVE' THEN
    IF NEW.snapshot IS NULL THEN
      RAISE EXCEPTION 'an ACTIVE llm_config_revision needs a snapshot'
        USING ERRCODE = 'check_violation';
    END IF;
    FOREACH purpose IN ARRAY {_LOCAL_ONLY_TEXT} LOOP
      deployment := NEW.snapshot -> 'routes' ->> purpose;
      CONTINUE WHEN deployment IS NULL;
      provider := (NEW.snapshot -> 'deployments' -> deployment ->> 'provider_id')::uuid;
      IF NOT EXISTS (SELECT 1 FROM llm_provider WHERE id = provider AND kind = 'LOCAL') THEN
        RAISE EXCEPTION 'snapshot routes % to a provider that is not LOCAL', purpose
          USING ERRCODE = 'check_violation';
      END IF;
    END LOOP;
  END IF;
  RETURN NEW;
END
$$
"""  # noqa: S608 # nosec B608 - constant text, no input

PROVIDER_FUNCTION = f"""
CREATE FUNCTION llm_provider_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.kind = 'CLOUD' AND OLD.kind = 'LOCAL' AND (
    EXISTS (
      SELECT 1 FROM llm_routing r JOIN llm_model m ON m.id = r.model_id
      WHERE m.provider_id = NEW.id AND r.purpose = ANY({_LOCAL_ONLY_SQL})
    ) OR EXISTS (
      SELECT 1 FROM llm_config_revision c,
        LATERAL jsonb_each(coalesce(c.snapshot -> 'routes', '{{}}'::jsonb)) AS route(purpose, dep)
      WHERE c.status = 'ACTIVE' AND route.purpose = ANY({_LOCAL_ONLY_TEXT})
        AND c.snapshot -> 'deployments' -> (route.dep #>> '{{}}') ->> 'provider_id'
            = NEW.id::text
    )
  ) THEN
    RAISE EXCEPTION 'llm_provider % serves a local-only purpose and cannot become CLOUD', NEW.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""  # noqa: S608 # nosec B608 - constant text, no input


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _user_fk(column: str, table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=op.f(f"fk_{table}_{column}_app_user"), ondelete="SET NULL"
    )


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind)

    op.create_table(
        "llm_provider",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column[str]("kind", provider_kind, nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column(
            "litellm_provider", sa.String(length=32), server_default="openai", nullable=False
        ),
        sa.Column("api_key_wrapped", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_hint", sa.String(length=8), nullable=True),
        sa.Column("inherit_env_key", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("structured_output", sa.String(length=16), nullable=True),
        sa.Column("timeout_seconds", sa.Float(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        *_timestamps(),
        _user_fk("created_by", "llm_provider"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_provider")),
        sa.UniqueConstraint("name", name=op.f("uq_llm_provider_name")),
    )

    op.create_table(
        "llm_model",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column[str]("capability", capability, nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("embed_dimension", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_ok", sa.Boolean(), nullable=True),
        sa.Column("last_probe_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        *_timestamps(),
        _user_fk("created_by", "llm_model"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["llm_provider.id"],
            name=op.f("fk_llm_model_provider_id_llm_provider"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_model")),
        sa.UniqueConstraint(
            "provider_id",
            "model_name",
            "capability",
            name=op.f("uq_llm_model_provider_id_model_name_capability"),
        ),
    )
    op.create_index(op.f("ix_llm_model_provider_id"), "llm_model", ["provider_id"])

    op.create_table(
        "llm_config_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column[str]("status", revision_status, server_default="DRAFT", nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("based_on", sa.BigInteger(), nullable=True),
        sa.Column("rolled_back_from", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("activated_by", sa.Uuid(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _user_fk("created_by", "llm_config_revision"),
        _user_fk("activated_by", "llm_config_revision"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_config_revision")),
        sa.UniqueConstraint("number", name=op.f("uq_llm_config_revision_number")),
    )
    op.create_index(
        "uq_llm_config_revision_one_active",
        "llm_config_revision",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "llm_routing",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column[str]("purpose", route_purpose, nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["llm_config_revision.id"],
            name=op.f("fk_llm_routing_revision_id_llm_config_revision"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_model.id"],
            name=op.f("fk_llm_routing_model_id_llm_model"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("revision_id", "purpose", name=op.f("pk_llm_routing")),
    )
    op.create_index(op.f("ix_llm_routing_model_id"), "llm_routing", ["model_id"])

    op.execute(ROUTING_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_routing_guard BEFORE INSERT OR UPDATE OR DELETE ON llm_routing
          FOR EACH ROW EXECUTE FUNCTION llm_routing_guard()
        """
    )
    op.execute(REVISION_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_revision_guard
          BEFORE INSERT OR UPDATE OR DELETE ON llm_config_revision
          FOR EACH ROW EXECUTE FUNCTION llm_revision_guard()
        """
    )
    op.execute(PROVIDER_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_provider_guard BEFORE UPDATE ON llm_provider
          FOR EACH ROW EXECUTE FUNCTION llm_provider_guard()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER llm_provider_guard ON llm_provider")
    op.execute("DROP FUNCTION llm_provider_guard()")
    op.execute("DROP TRIGGER llm_revision_guard ON llm_config_revision")
    op.execute("DROP FUNCTION llm_revision_guard()")
    op.execute("DROP TRIGGER llm_routing_guard ON llm_routing")
    op.execute("DROP FUNCTION llm_routing_guard()")
    op.drop_table("llm_routing")
    op.drop_table("llm_config_revision")
    op.drop_table("llm_model")
    op.drop_table("llm_provider")
    bind = op.get_bind()
    for enum in reversed(ENUMS):
        enum.drop(bind)
