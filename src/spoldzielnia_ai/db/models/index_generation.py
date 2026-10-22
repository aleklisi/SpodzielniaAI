from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Identity, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import IndexGenerationStatus


class IndexGeneration(Entity):
    """One version of the text index (``private_notes_index`` + ``canonical_wiki_index``),
    built by one embedding model (10.3, invariant P8).

    It records the model that built it (``embed_key``, the deployment without secrets in
    ``deployment``; ``None`` for the ``.env`` model) and its dimension. Every query is embedded
    with the model of the generation it searches. Changing the embedding model builds a new
    generation next to the serving one; when it is complete the two swap, and the old one is
    dropped after a grace period. No content, no RLS: ids and model metadata only.
    """

    __tablename__ = "index_generation"
    __table_args__ = (
        # At most one SERVING and one BUILDING generation.
        Index(
            "uq_index_generation_serving",
            "status",
            unique=True,
            postgresql_where=text("status = 'SERVING'"),
        ),
        Index(
            "uq_index_generation_building",
            "status",
            unique=True,
            postgresql_where=text("status = 'BUILDING'"),
        ),
    )

    number: Mapped[int] = mapped_column(Identity(always=True), unique=True)
    status: Mapped[IndexGenerationStatus]
    embed_key: Mapped[str] = mapped_column(String(600))
    model: Mapped[str] = mapped_column(String(300))
    deployment: Mapped[dict[str, Any] | None]
    revision: Mapped[int]  # the LLM configuration revision it was created for (0: .env)
    dimension: Mapped[int]
    private_collection: Mapped[str] = mapped_column(String(100))
    canonical_collection: Mapped[str] = mapped_column(String(100))
    total: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    done: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    failed: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    cursor: Mapped[dict[str, Any] | None]
    serving_since: Mapped[datetime | None]
    retired_at: Mapped[datetime | None]
    drop_after: Mapped[datetime | None]
    dropped_at: Mapped[datetime | None]
