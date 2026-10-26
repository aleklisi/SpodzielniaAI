"""Removing a model provider (chunk 11.1, US-05).

Revision ID: 0026_provider_removal
Revises: 0025_processors_seen
Create Date: 2026-10-26

A provider that no activated revision ever used is deleted outright. One that history refers to
(the routes of ACTIVE/ARCHIVED revisions are immutable, 0012) is **archived** instead:

- ``llm_provider.deleted_at``: set once, never cleared. Its name is free again, so the unique
  constraint on ``name`` becomes a unique index over providers that are not archived.
- ``llm_provider_removed_guard``: ``deleted_at`` cannot be cleared or changed, and an archived
  provider cannot be cleared for private data again.
- ``llm_routing_removed_guard``: no route (in a draft) may point to a model of an archived
  provider.
- ``llm_revision_removed_guard``: no revision becomes ACTIVE with a snapshot that names an
  archived provider (a rollback to a revision that used it).

The service checks all three first, with readable errors; these triggers hold even if it is
bypassed. Downgrade deletes nothing: archived rows stay, but the old unique constraint needs the
names to differ, so archived providers get their id appended to the name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0026_provider_removal"
down_revision: str | None = "0025_processors_seen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_FUNCTION = """
CREATE FUNCTION llm_provider_removed_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.deleted_at IS NOT NULL THEN
    IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
      RAISE EXCEPTION 'llm_provider % was removed and cannot be restored', OLD.id
        USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.private_data_cleared THEN
      RAISE EXCEPTION 'removed llm_provider % cannot be cleared for private data', OLD.id
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$
"""

ROUTING_FUNCTION = """
CREATE FUNCTION llm_routing_removed_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM llm_model m JOIN llm_provider p ON p.id = m.provider_id
    WHERE m.id = NEW.model_id AND p.deleted_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'purpose % cannot use a model of a removed provider', NEW.purpose
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""

REVISION_FUNCTION = """
CREATE FUNCTION llm_revision_removed_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'ACTIVE' AND NEW.snapshot IS NOT NULL AND EXISTS (
    SELECT 1
    FROM jsonb_each(coalesce(NEW.snapshot -> 'deployments', '{}'::jsonb)) AS d(key, dep)
    JOIN llm_provider p ON p.id::text = d.dep ->> 'provider_id'
    WHERE p.deleted_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'an ACTIVE llm_config_revision cannot use a removed provider'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.add_column(
        "llm_provider", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("uq_llm_provider_name", "llm_provider", type_="unique")
    op.create_index(
        "uq_llm_provider_name",
        "llm_provider",
        ["name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(PROVIDER_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_provider_removed_guard BEFORE UPDATE ON llm_provider
          FOR EACH ROW EXECUTE FUNCTION llm_provider_removed_guard()
        """
    )
    op.execute(ROUTING_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_routing_removed_guard BEFORE INSERT OR UPDATE ON llm_routing
          FOR EACH ROW EXECUTE FUNCTION llm_routing_removed_guard()
        """
    )
    op.execute(REVISION_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER llm_revision_removed_guard
          BEFORE INSERT OR UPDATE ON llm_config_revision
          FOR EACH ROW EXECUTE FUNCTION llm_revision_removed_guard()
        """
    )


def downgrade() -> None:
    for table, name in (
        ("llm_config_revision", "llm_revision_removed_guard"),
        ("llm_routing", "llm_routing_removed_guard"),
        ("llm_provider", "llm_provider_removed_guard"),
    ):
        op.execute(f"DROP TRIGGER {name} ON {table}")
        op.execute(f"DROP FUNCTION {name}()")
    op.execute(
        "UPDATE llm_provider SET name = left(name, 27) || '-' || id::text"
        " WHERE deleted_at IS NOT NULL"
    )
    op.drop_index("uq_llm_provider_name", "llm_provider")
    op.create_unique_constraint("uq_llm_provider_name", "llm_provider", ["name"])
    op.drop_column("llm_provider", "deleted_at")
