"""Image embeddings over an API or built in; the atlas on index generations (10.6, P1/P8).

Revision ID: 0024_image_embed
Revises: 0023_ocr_purpose
Create Date: 2026-10-23

- ``llm_route_purpose`` and ``llm_capability`` gain ``IMAGE_EMBED``. The routing and revision
  guards are redefined with IMAGE_EMBED among the private purposes (members' photos and crops go
  only to providers cleared for private data); an IMAGE_EMBED route needs an IMAGE_EMBED model.
- ``index_generation.family``: ``TEXT`` (the 10.3 text index) or ``IMAGE`` (the visual atlas,
  its collection in ``canonical_collection``). At most one SERVING and one BUILDING generation
  per family.

``PRIVATE`` repeats ``llm.gateway.PRIVATE_PURPOSES`` (migrations are frozen); a unit test checks
that the two agree. Downgrade restores the 0023 guards and drops IMAGE generations' rows (their
collections are left to the operator); ``IMAGE_EMBED`` stays in both enums (unused).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0024_image_embed"
down_revision: str | None = "0023_ocr_purpose"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRIVATE = (
    "PII_SCRUB",
    "PRIVATE_NOTE",
    "PROMOTION",
    "VLM",
    "EMBED",
    "TRANSCRIBE",
    "OCR",
    "IMAGE_EMBED",
)


def _previous(name: str) -> str:
    """A guard function's text as 0023 installed it (frozen)."""
    module = importlib.import_module("spoldzielnia_ai.db.migrations.versions.0023_ocr_purpose")
    return str(module._upgraded(name))  # pyright: ignore[reportPrivateUsage]


OLD_ARRAY = "ARRAY['PII_SCRUB', 'PRIVATE_NOTE', 'PROMOTION', 'VLM', 'EMBED', 'TRANSCRIBE', 'OCR']"
NEW_ARRAY = "ARRAY[" + ", ".join(f"'{p}'" for p in PRIVATE) + "]"
OLD_CAPABILITY = "WHEN 'TRANSCRIBE' THEN 'TRANSCRIBE'"
NEW_CAPABILITY = "WHEN 'TRANSCRIBE' THEN 'TRANSCRIBE' WHEN 'IMAGE_EMBED' THEN 'IMAGE_EMBED'"


def _upgraded(name: str) -> str:
    text = _previous(name)
    assert OLD_ARRAY in text  # noqa: S101 - frozen text, checked at upgrade time
    return text.replace(OLD_ARRAY, NEW_ARRAY).replace(OLD_CAPABILITY, NEW_CAPABILITY)


def _unique_per_family(columns: list[str]) -> None:
    for status in ("SERVING", "BUILDING"):
        op.create_index(
            f"uq_index_generation_{status.lower()}",
            "index_generation",
            columns,
            unique=True,
            postgresql_where=sa.text(f"status = '{status}'"),
        )


def upgrade() -> None:
    op.execute("ALTER TYPE llm_route_purpose ADD VALUE IF NOT EXISTS 'IMAGE_EMBED'")
    op.execute("ALTER TYPE llm_capability ADD VALUE IF NOT EXISTS 'IMAGE_EMBED'")
    routing = _upgraded("ROUTING_FUNCTION")
    assert NEW_CAPABILITY in routing  # noqa: S101
    op.execute(routing)
    op.execute(_upgraded("REVISION_FUNCTION"))
    op.add_column(
        "index_generation",
        sa.Column("family", sa.String(16), server_default=sa.text("'TEXT'"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_index_generation_family"), "index_generation", "family IN ('TEXT', 'IMAGE')"
    )
    op.drop_index("uq_index_generation_serving", table_name="index_generation")
    op.drop_index("uq_index_generation_building", table_name="index_generation")
    _unique_per_family(["family"])


def downgrade() -> None:
    op.drop_index("uq_index_generation_serving", table_name="index_generation")
    op.drop_index("uq_index_generation_building", table_name="index_generation")
    op.execute("DELETE FROM index_generation WHERE family = 'IMAGE'")
    op.drop_constraint(op.f("ck_index_generation_family"), "index_generation")
    op.drop_column("index_generation", "family")
    _unique_per_family(["status"])
    op.execute(_previous("ROUTING_FUNCTION"))
    op.execute(_previous("REVISION_FUNCTION"))
