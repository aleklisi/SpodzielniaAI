"""Documents without models: the OCR purpose (chunk 10.5, P1/P9).

Revision ID: 0023_ocr_purpose
Revises: 0022_transcribe_purpose
Create Date: 2026-10-23

- ``llm_route_purpose`` gains ``OCR``: scanned pages are read by a vision (VLM) model.
- The routing and revision guards are redefined with OCR among the private purposes (members'
  documents go only to providers cleared for private data), and an OCR route needs a VLM model.

``PRIVATE`` repeats ``llm.gateway.PRIVATE_PURPOSES`` (migrations are frozen); a unit test checks
that the two agree. Downgrade restores the 0022 guards; ``OCR`` stays in the enum (unused).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0023_ocr_purpose"
down_revision: str | None = "0022_transcribe_purpose"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVATE = ("PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "VLM", "EMBED", "TRANSCRIBE", "OCR")


def _previous(name: str) -> str:
    """A guard function's text as 0022 installed it (frozen)."""
    module = importlib.import_module(
        "spoldzielnia_ai.db.migrations.versions.0022_transcribe_purpose"
    )
    return str(module._upgraded(name))  # pyright: ignore[reportPrivateUsage]


OLD_ARRAY = "ARRAY['PII_SCRUB', 'PRIVATE_NOTE', 'PROMOTION', 'VLM', 'EMBED', 'TRANSCRIBE']"
NEW_ARRAY = "ARRAY[" + ", ".join(f"'{p}'" for p in PRIVATE) + "]"
OLD_CAPABILITY = "WHEN 'VLM' THEN 'VLM' WHEN 'EMBED'"
NEW_CAPABILITY = "WHEN 'VLM' THEN 'VLM' WHEN 'OCR' THEN 'VLM' WHEN 'EMBED'"


def _upgraded(name: str) -> str:
    text = _previous(name)
    assert OLD_ARRAY in text  # noqa: S101 - frozen text, checked at upgrade time
    return text.replace(OLD_ARRAY, NEW_ARRAY).replace(OLD_CAPABILITY, NEW_CAPABILITY)


def upgrade() -> None:
    op.execute("ALTER TYPE llm_route_purpose ADD VALUE IF NOT EXISTS 'OCR'")
    routing = _upgraded("ROUTING_FUNCTION")
    assert NEW_CAPABILITY in routing  # noqa: S101
    op.execute(routing)
    op.execute(_upgraded("REVISION_FUNCTION"))


def downgrade() -> None:
    op.execute(_previous("ROUTING_FUNCTION"))
    op.execute(_previous("REVISION_FUNCTION"))
