"""What the client sees of an answer, and what the model must return (5.1).

AGENTS.md §3.3: an answer carries the synthesis and article-level citations, never block text,
block ids, scores, vectors or chunk offsets. ``[n]`` markers in ``answer_markdown`` refer to
``citations[n-1]``. A contract test walks these schemas for forbidden fields.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from pydantic import BaseModel, Field


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=10_000)


class Citation(BaseModel):
    article_id: UUID
    article_title: str
    wiki_url: str
    last_verified_at: datetime | None
    authors: list[str]
    approving_experts: list[str]


class RagAnswer(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    no_official_position: bool = False
    request_id: UUID


class NoteCitation(BaseModel):
    note_id: UUID
    note_title: str


class NoteAnswer(BaseModel):
    """Private notebook Q&A: the member's own notes, still synthesis only."""

    answer_markdown: str
    citations: list[NoteCitation]
    no_answer: bool = False
    request_id: UUID


class AnswerDraft(BaseModel):
    """The model's reply. ``[Sx]`` markers must refer to the sources it was given."""

    answer_markdown: str = Field(
        max_length=20_000,
        description="odpowiedź w Markdown; po każdym zdaniu etykieta źródła, np. [S1]",
    )
    citations: list[str] = Field(
        default_factory=list[str], max_length=50, description="użyte etykiety źródeł, np. S1"
    )
    insufficient: bool = Field(
        default=False, description="true, jeśli źródła nie zawierają odpowiedzi na pytanie"
    )
