"""Versions of the text index, each built by one embedding model (chunk 10.3, invariant P8).

Revision ID: 0021_index_generations
Revises: 0020_model_wait
Create Date: 2026-10-22

``index_generation`` records each version of the text index (the private and the canonical
Qdrant collection): the embedding model that built it, its dimension, its collections, its
status and the progress of its build. Queries are embedded with the model of the generation
they search; a model change builds a new generation and swaps it in when complete.

No rows are created here: the worker adopts the existing collections as the first generation
(they were built with the active embedding model, which could not change before 10.3), or
creates one when an embedding model is first configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0021_index_generations"
down_revision: str | None = "0020_model_wait"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS = postgresql.ENUM(
    "BUILDING", "SERVING", "RETIRED", "CANCELLED", name="index_generation_status", create_type=False
)
ENUMS = (STATUS,)


def _now(name: str) -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    STATUS.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "index_generation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column[str]("status", STATUS, nullable=False),
        sa.Column("embed_key", sa.String(600), nullable=False),
        sa.Column("model", sa.String(300), nullable=False),
        sa.Column("deployment", postgresql.JSONB(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("private_collection", sa.String(100), nullable=False),
        sa.Column("canonical_collection", sa.String(100), nullable=False),
        sa.Column("total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cursor", postgresql.JSONB(), nullable=True),
        sa.Column("serving_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drop_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dropped_at", sa.DateTime(timezone=True), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_generation")),
        sa.UniqueConstraint("number", name=op.f("uq_index_generation_number")),
    )
    op.create_index(
        "uq_index_generation_serving",
        "index_generation",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'SERVING'"),
    )
    op.create_index(
        "uq_index_generation_building",
        "index_generation",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'BUILDING'"),
    )


def downgrade() -> None:
    op.drop_table("index_generation")
    STATUS.drop(op.get_bind(), checkfirst=False)
