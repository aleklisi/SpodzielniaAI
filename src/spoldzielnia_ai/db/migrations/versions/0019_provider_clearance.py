"""Providers cleared for private data replace "local only" (chunk 10.1, AGENTS.md rule 4).

Revision ID: 0019_provider_clearance
Revises: 0018_recognition
Create Date: 2026-10-22

``llm_provider`` gains the clearance an ADMIN gives (with MFA and step-up): the flag, the data
region, the retention, the contract reference, who and when. The triggers of migration 0012 are
redefined so that the purposes in ``PRIVATE`` need a **cleared** provider instead of a LOCAL one:

- ``llm_routing``: a private purpose may only point at a model of a cleared provider.
- ``llm_config_revision``: a row becomes ACTIVE only if its private routes use cleared providers.
- ``llm_provider``: a cleared provider cannot change its endpoint (un-clear it first). Un-clearing
  is never blocked: the gateway then refuses the provider for private purposes at once.

Existing LOCAL providers are cleared by this migration, so an upgraded installation keeps
working as before. The runtime still checks each cleared endpoint against
``llm.private_data_endpoints`` (default: the local endpoints), so this cannot widen anything.

``PRIVATE`` repeats ``llm.gateway.PRIVATE_PURPOSES`` (migrations are frozen); a unit test checks
that the two agree.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0019_provider_clearance"
down_revision: str | None = "0018_recognition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVATE = ("PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "VLM", "EMBED")
_PRIVATE_ARRAY = "ARRAY[" + ", ".join(f"'{p}'" for p in PRIVATE) + "]"
_PRIVATE_SQL = _PRIVATE_ARRAY + "::llm_route_purpose[]"
_PRIVATE_TEXT = _PRIVATE_ARRAY + "::text[]"

ROUTING_FUNCTION = f"""
CREATE OR REPLACE FUNCTION llm_routing_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  rev_status llm_revision_status;
  model_capability llm_capability;
  cleared boolean;
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
  SELECT m.capability, p.private_data_cleared INTO model_capability, cleared
    FROM llm_model m JOIN llm_provider p ON p.id = m.provider_id
    WHERE m.id = NEW.model_id;
  IF NEW.purpose = ANY({_PRIVATE_SQL}) AND cleared IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'purpose % may only use a provider cleared for private data', NEW.purpose
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
"""  # noqa: S608 - constant text, no input

REVISION_FUNCTION = f"""
CREATE OR REPLACE FUNCTION llm_revision_guard() RETURNS trigger
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
    FOREACH purpose IN ARRAY {_PRIVATE_TEXT} LOOP
      deployment := NEW.snapshot -> 'routes' ->> purpose;
      CONTINUE WHEN deployment IS NULL;
      provider := (NEW.snapshot -> 'deployments' -> deployment ->> 'provider_id')::uuid;
      IF NOT EXISTS (
        SELECT 1 FROM llm_provider WHERE id = provider AND private_data_cleared
      ) THEN
        RAISE EXCEPTION 'snapshot routes % to a provider not cleared for private data', purpose
          USING ERRCODE = 'check_violation';
      END IF;
    END LOOP;
  END IF;
  RETURN NEW;
END
$$
"""  # noqa: S608 - constant text, no input

PROVIDER_FUNCTION = """
CREATE OR REPLACE FUNCTION llm_provider_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.private_data_cleared AND NEW.private_data_cleared
     AND NEW.base_url IS DISTINCT FROM OLD.base_url THEN
    RAISE EXCEPTION 'llm_provider % is cleared for private data: un-clear it before changing '
      'its endpoint', NEW.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.add_column(
        "llm_provider",
        sa.Column(
            "private_data_cleared", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("llm_provider", sa.Column("cleared_region", sa.String(64), nullable=True))
    op.add_column("llm_provider", sa.Column("cleared_retention", sa.String(64), nullable=True))
    op.add_column("llm_provider", sa.Column("cleared_contract_ref", sa.String(200), nullable=True))
    op.add_column("llm_provider", sa.Column("cleared_by", sa.Uuid(), nullable=True))
    op.add_column(
        "llm_provider", sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_llm_provider_cleared_by_app_user"),
        "llm_provider",
        "app_user",
        ["cleared_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE llm_provider SET
          private_data_cleared = true,
          cleared_region = 'serwer spółdzielni',
          cleared_retention = 'zgodnie z konfiguracją serwera',
          cleared_contract_ref = 'migracja 0019 (dostawca lokalny)',
          cleared_at = now()
        WHERE kind = 'LOCAL'
        """
    )
    op.create_check_constraint(
        op.f("ck_llm_provider_clearance_complete"),
        "llm_provider",
        "NOT private_data_cleared OR (cleared_region IS NOT NULL AND cleared_retention IS NOT NULL"
        " AND cleared_contract_ref IS NOT NULL AND cleared_at IS NOT NULL)",
    )
    op.execute(ROUTING_FUNCTION)
    op.execute(REVISION_FUNCTION)
    op.execute(PROVIDER_FUNCTION)


def downgrade() -> None:
    old = importlib.import_module("spoldzielnia_ai.db.migrations.versions.0012_llm_config")
    for function in (old.ROUTING_FUNCTION, old.REVISION_FUNCTION, old.PROVIDER_FUNCTION):
        op.execute(function.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.drop_constraint(op.f("ck_llm_provider_clearance_complete"), "llm_provider")
    op.drop_constraint(op.f("fk_llm_provider_cleared_by_app_user"), "llm_provider")
    for column in (
        "cleared_at",
        "cleared_by",
        "cleared_contract_ref",
        "cleared_retention",
        "cleared_region",
        "private_data_cleared",
    ):
        op.drop_column("llm_provider", column)
