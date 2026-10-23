"""Speech-to-text over an API: the TRANSCRIBE purpose and capability (chunk 10.4, P1/P9).

Revision ID: 0022_transcribe_purpose
Revises: 0021_index_generations
Create Date: 2026-10-23

- ``llm_route_purpose`` and ``llm_capability`` gain ``TRANSCRIBE``.
- The routing and revision guards of 0019 are redefined with TRANSCRIBE among the private
  purposes (members' recordings go only to providers cleared for private data), and a
  TRANSCRIBE route needs a TRANSCRIBE model.

``PRIVATE`` repeats ``llm.gateway.PRIVATE_PURPOSES`` (migrations are frozen); a unit test checks
that the two agree. Downgrade restores the 0019 guards; Postgres cannot drop enum values, so
``TRANSCRIBE`` stays in both types (unused).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0022_transcribe_purpose"
down_revision: str | None = "0021_index_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVATE = ("PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "VLM", "EMBED", "TRANSCRIBE")


def _previous(name: str) -> str:
    """A guard function's text from 0019 (frozen)."""
    module = importlib.import_module(
        "spoldzielnia_ai.db.migrations.versions.0019_provider_clearance"
    )
    return str(getattr(module, name))


OLD_ARRAY = "ARRAY['PII_SCRUB', 'PRIVATE_NOTE', 'PROMOTION', 'VLM', 'EMBED']"
NEW_ARRAY = "ARRAY[" + ", ".join(f"'{p}'" for p in PRIVATE) + "]"
OLD_CAPABILITY = "WHEN 'VLM' THEN 'VLM' WHEN 'EMBED' THEN 'EMBED' ELSE 'CHAT' END"
NEW_CAPABILITY = (
    "WHEN 'VLM' THEN 'VLM' WHEN 'EMBED' THEN 'EMBED' WHEN 'TRANSCRIBE' THEN 'TRANSCRIBE' "
    "ELSE 'CHAT' END"
)


def _upgraded(name: str) -> str:
    text = _previous(name)
    assert OLD_ARRAY in text  # noqa: S101 - frozen text, checked at upgrade time
    return text.replace(OLD_ARRAY, NEW_ARRAY).replace(OLD_CAPABILITY, NEW_CAPABILITY)


def upgrade() -> None:
    op.execute("ALTER TYPE llm_route_purpose ADD VALUE IF NOT EXISTS 'TRANSCRIBE'")
    op.execute("ALTER TYPE llm_capability ADD VALUE IF NOT EXISTS 'TRANSCRIBE'")
    routing = _upgraded("ROUTING_FUNCTION")
    assert NEW_CAPABILITY in routing  # noqa: S101
    op.execute(routing)
    op.execute(_upgraded("REVISION_FUNCTION"))


def downgrade() -> None:
    op.execute(_previous("ROUTING_FUNCTION"))
    op.execute(_previous("REVISION_FUNCTION"))
