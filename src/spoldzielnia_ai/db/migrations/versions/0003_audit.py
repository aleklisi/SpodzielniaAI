"""Immutable, hash-chained security audit log (gap G10).

Revision ID: 0003_audit
Revises: 0002_rls
Create Date: 2026-10-16

- The app role may only ``INSERT`` and ``SELECT``. ``UPDATE``/``DELETE``/``TRUNCATE`` are revoked.
- Triggers reject ``UPDATE``/``DELETE``/``TRUNCATE`` for every role, the owner included. This is
  defence in depth: the owner could still drop the trigger, and the hash chain detects that.
- ``BEFORE INSERT`` chains each row: ``hash = sha256(prev_hash || canonical row text)``.
  ``pg_advisory_xact_lock`` serialises appends until commit. ``id`` is drawn after the lock is
  taken, so id order is chain order. ``occurred_at``, ``prev_hash`` and ``hash`` are always set
  here, so callers cannot backdate an entry or forge a link.

The canonical text expression is repeated in ``spoldzielnia_ai.audit.verify`` (migrations are
frozen). The integration tests check that the two copies agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003_audit"
down_revision: str | None = "0002_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "spoldzielnia_app"  # infra/postgres/init/10-roles.sh

CHAIN_FUNCTION = """
CREATE FUNCTION security_audit_log_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  last_hash bytea;
BEGIN
  -- Under REPEATABLE READ / SERIALIZABLE the snapshot predates the lock, so the last committed
  -- row could be missed and the chain would fork.
  IF current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'security_audit_log appends require READ COMMITTED (got %)',
      current_setting('transaction_isolation');
  END IF;

  -- Held until commit: appends are chained one transaction at a time.
  PERFORM pg_advisory_xact_lock(hashtextextended('security_audit_log', 0));

  NEW.id := nextval(pg_get_serial_sequence('security_audit_log', 'id'));
  NEW.occurred_at := clock_timestamp();
  SELECT hash INTO last_hash FROM security_audit_log ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := coalesce(last_hash, decode(repeat('00', 32), 'hex'));
  NEW.hash := sha256(
    NEW.prev_hash || convert_to(
      jsonb_build_object(
        'id', NEW.id,
        'occurred_at', to_char(NEW.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'actor_id', NEW.actor_id,
        'event_type', NEW.event_type,
        'target_type', NEW.target_type,
        'target_id', NEW.target_id,
        'payload', NEW.payload
      )::text,
      'UTF8'
    )
  );
  RETURN NEW;
END
$$
"""

IMMUTABLE_FUNCTION = """
CREATE FUNCTION security_audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'security_audit_log is append-only: % rejected', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$
"""


def upgrade() -> None:
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON security_audit_log FROM {APP_ROLE}")

    op.execute(CHAIN_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER security_audit_log_chain BEFORE INSERT ON security_audit_log
          FOR EACH ROW EXECUTE FUNCTION security_audit_log_chain()
        """
    )

    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER security_audit_log_no_modify BEFORE UPDATE OR DELETE ON security_audit_log
          FOR EACH ROW EXECUTE FUNCTION security_audit_log_immutable()
        """
    )
    op.execute(
        """
        CREATE TRIGGER security_audit_log_no_truncate BEFORE TRUNCATE ON security_audit_log
          FOR EACH STATEMENT EXECUTE FUNCTION security_audit_log_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER security_audit_log_no_truncate ON security_audit_log")
    op.execute("DROP TRIGGER security_audit_log_no_modify ON security_audit_log")
    op.execute("DROP FUNCTION security_audit_log_immutable()")
    op.execute("DROP TRIGGER security_audit_log_chain ON security_audit_log")
    op.execute("DROP FUNCTION security_audit_log_chain()")
    # Back to the default privileges of 0001 (TRUNCATE was never granted).
    op.execute(f"GRANT UPDATE, DELETE ON security_audit_log TO {APP_ROLE}")
