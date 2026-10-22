"""``RAGService``: grounded, cited answers from canonical knowledge (5.1).

1. Embed the question locally and search ``CanonicalVectorRepo`` (``active_canonical_filter``
   always applies) for ``top_k`` hits at or above ``rag.score_threshold``.
2. Re-validate every hit against Postgres and load its text there (Qdrant holds no text).
   Hits Postgres no longer allows are dropped and repair operations go to the outbox.
3. The ``RetrievalGate`` (``AntiDistillationGuard``, 5.6) sees the retrieval and decides which
   blocks may reach synthesis. Honeypot blocks never do, gate or no gate (gap G15).
4. Nothing left: "no official position", and the LLM is not called.
5. Synthesis with strict grounding (``rag.synthesis``). Then ``answer`` turns the (verified,
   watermarked) text into a ``RagAnswer`` with article-level citations only.

Only ``shield.guard`` may call ``ask`` (test_guard_is_the_only_caller): the API never reaches
this module directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select

from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ConsentStatus, ReviewDecision
from spoldzielnia_ai.db.models import ArticleBlock, ExpertReview, User, WikiArticle
from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.llm.prompts import RAG_ANSWER
from spoldzielnia_ai.rag.schemas import NO_OFFICIAL_POSITION, Citation, RagAnswer
from spoldzielnia_ai.rag.synthesis import Style, Synthesizer, label_sources, renumber
from spoldzielnia_ai.vector.revalidate import load_retrievable, repair_ops
from spoldzielnia_ai.vector.sync import VectorSync

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from spoldzielnia_ai.core.settings import RagSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.gateway import LLMGateway
    from spoldzielnia_ai.rag.schemas import AnswerDraft
    from spoldzielnia_ai.rag.synthesis import Source
    from spoldzielnia_ai.vector.embedding import Embedder
    from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
    from spoldzielnia_ai.vector.revalidate import LiveBlock

log = get_logger(__name__)

UNKNOWN_AUTHOR = "Członek spółdzielni"
UNKNOWN_EXPERT = "Ekspert spółdzielni"


@dataclass(frozen=True, slots=True)
class Retrieval:
    """Internal only: never serialised to a client."""

    question_vector: list[float]
    blocks: list[LiveBlock]  # re-validated, best first, honeypots included
    stale: int = 0


class RetrievalGate(Protocol):
    async def admit(self, retrieval: Retrieval) -> list[LiveBlock]:
        """The blocks that may reach synthesis (may raise to stop the request)."""
        ...


@dataclass(frozen=True, slots=True)
class Outcome:
    retrieval: Retrieval
    admitted: list[LiveBlock]  # what synthesis saw
    sources: list[Source]
    draft: AnswerDraft | None

    @property
    def answered(self) -> bool:
        return self.draft is not None


class RAGService:
    def __init__(
        self,
        factory: SessionFactory,
        repo: CanonicalVectorRepo,
        embedder: Embedder,
        gateway: LLMGateway,
        settings: RagSettings,
    ) -> None:
        self._factory = factory
        self._repo = repo
        self._embedder = embedder
        self._synth = Synthesizer(gateway, RAG_ANSWER, LLMPurpose.RAG, private_data=False)
        self._settings = settings

    async def embed(self, question: str) -> list[float]:
        return (await self._embedder.embed([question]))[0]

    async def retrieve(self, vector: list[float], top_k: int) -> Retrieval:
        hits = [
            h
            for h in await self._repo.search(vector, limit=top_k)
            if h.score >= self._settings.score_threshold
        ]
        async with self._factory() as session:
            checked = await load_retrievable(session, hits)
        if checked.stale:
            await self._repair(checked.stale)
        return Retrieval(vector, checked.live, len(checked.stale))

    async def _repair(self, block_ids: list[UUID]) -> None:
        """Queue the fix for the next reconciler run (never fails the request)."""
        try:
            async with self._factory() as session, session.begin():
                await VectorSync.record(session, await repair_ops(session, block_ids))
            log.warning("rag.stale_hits", count=len(block_ids))
        except Exception as exc:
            log.error("rag.repair_failed", error=type(exc).__name__)

    async def ask(
        self,
        question: str,
        *,
        top_k: int | None = None,
        style: Style | None = None,
        gate: RetrievalGate | None = None,
        vector: list[float] | None = None,
    ) -> Outcome:
        style = style or Style(max_words=self._settings.answer_max_words)
        vector = vector if vector is not None else await self.embed(question)
        retrieval = await self.retrieve(vector, top_k or self._settings.top_k)
        admitted = await gate.admit(retrieval) if gate is not None else retrieval.blocks
        admitted = [b for b in admitted if not b.is_honeypot]
        sources = label_sources([(b.content, b.article_id) for b in admitted])
        draft = await self._synth.draft(question, sources, style) if sources else None
        return Outcome(retrieval, admitted, sources, draft)

    async def answer(
        self, outcome: Outcome, text: str | None = None, request_id: UUID | None = None
    ) -> RagAnswer:
        """The client-facing answer. ``text`` is the draft after output checks (5.5)."""
        request_id = request_id or uuid7()
        if outcome.draft is None:
            return self.no_position(request_id)
        body, cited = renumber(text or outcome.draft.answer_markdown, outcome.sources)
        return RagAnswer(
            answer_markdown=body, citations=await self.citations(cited), request_id=request_id
        )

    @staticmethod
    def no_position(request_id: UUID | None = None) -> RagAnswer:
        return RagAnswer(
            answer_markdown=NO_OFFICIAL_POSITION,
            citations=[],
            no_official_position=True,
            request_id=request_id or uuid7(),
        )

    async def citations(self, article_ids: Sequence[UUID]) -> list[Citation]:
        """Article-level citations, in the given order."""
        if not article_ids:
            return []
        ids = list(article_ids)
        async with self._factory() as session:
            articles = {
                a.id: a
                for a in await session.scalars(select(WikiArticle).where(WikiArticle.id.in_(ids)))
            }
            author_rows = await session.execute(
                select(ArticleBlock.article_id, User.id, User.display_name)
                .join(User, User.id == ArticleBlock.author_id)
                .where(
                    ArticleBlock.article_id.in_(ids),
                    ArticleBlock.consent_status == ConsentStatus.ACTIVE,
                )
                .distinct()
            )
            expert_rows = await session.execute(
                select(ExpertReview.article_id, User.id, User.display_name)
                .join(User, User.id == ExpertReview.reviewer_id)
                .where(
                    ExpertReview.article_id.in_(ids),
                    ExpertReview.decision == ReviewDecision.APPROVE,
                )
                .distinct()
            )
        authors = _names(author_rows.all(), UNKNOWN_AUTHOR)
        experts = _names(expert_rows.all(), UNKNOWN_EXPERT)
        return [
            Citation(
                article_id=a,
                article_title=articles[a].title,
                wiki_url=self._settings.wiki_url_template.format(article_id=a),
                last_verified_at=articles[a].last_verified_at,
                authors=authors.get(a, []),
                approving_experts=experts.get(a, []),
            )
            for a in ids
            if a in articles
        ]


def _names(rows: Sequence[tuple[UUID, UUID, str | None]], fallback: str) -> dict[UUID, list[str]]:
    by_article: dict[UUID, dict[UUID, str]] = {}
    for article_id, user_id, name in rows:
        by_article.setdefault(article_id, {})[user_id] = name or fallback
    return {a: sorted(names.values()) for a, names in by_article.items()}
