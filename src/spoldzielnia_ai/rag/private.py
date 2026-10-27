"""Notebook Q&A (5.1): grounded answers from the member's own notes.

Same synthesis and grounding rules as cooperative RAG, with three differences: the search is
``PrivateVectorRepo(user_id)`` (tenant filter), the text is decrypted through the member's
``RlsSession`` (``NotebookService``, AGENTS.md §3.1), and the model is always local
(``private_data=True``, AGENTS.md §3.4). Quarantined derivatives are never indexed, and
``derivative_texts`` re-checks that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.i18n import t
from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.llm.prompts import NOTEBOOK_ANSWER
from spoldzielnia_ai.rag.schemas import NoteAnswer, NoteCitation
from spoldzielnia_ai.rag.synthesis import Style, Synthesizer, label_sources, renumber
from spoldzielnia_ai.vector.chunking import chunk_text
from spoldzielnia_ai.vector.repositories import PrivateVectorRepo

if TYPE_CHECKING:
    from uuid import UUID

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import RagSettings
    from spoldzielnia_ai.i18n import Locale
    from spoldzielnia_ai.llm.gateway import LLMGateway
    from spoldzielnia_ai.notebook.service import NotebookService
    from spoldzielnia_ai.vector.embedding import Embedder


class NotebookQA:
    def __init__(
        self,
        client: AsyncQdrantClient,
        embedder: Embedder,
        gateway: LLMGateway,
        settings: RagSettings,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._synth = Synthesizer(
            gateway, NOTEBOOK_ANSWER, LLMPurpose.PRIVATE_NOTE, private_data=True
        )
        self._settings = settings

    async def ask(
        self, notebook: NotebookService, user_id: UUID, question: str, locale: Locale = "pl"
    ) -> NoteAnswer:
        """Answered in ``locale``: the member's own notes, so no anti-distillation check."""
        request_id = uuid7()
        query = await self._embedder.query([question])
        repo = PrivateVectorRepo(self._client, user_id, query.generation.private_collection)
        hits = [
            h
            for h in await repo.search(query.vector, limit=self._settings.top_k)
            if h.score >= self._settings.score_threshold
        ]
        derivatives = await notebook.derivative_texts([h.derivative_id for h in hits])
        passages: list[tuple[str, UUID]] = []
        for hit in hits:
            view = derivatives.get(hit.derivative_id)
            if view is None or view.content is None:
                continue
            chunks = chunk_text(view.content)
            if hit.chunk < len(chunks):
                passages.append((chunks[hit.chunk], hit.note_id))
        sources = label_sources(passages)
        style = Style(max_words=self._settings.answer_max_words, language=locale)
        draft = await self._synth.draft(question, sources, style) if sources else None
        if draft is None:
            return NoteAnswer(
                answer_markdown=t("rag.no_notes_answer", locale),
                citations=[],
                no_answer=True,
                request_id=request_id,
            )
        body, cited = renumber(draft.answer_markdown, sources)
        titles = await notebook.titles(cited)
        return NoteAnswer(
            answer_markdown=body,
            citations=[NoteCitation(note_id=n, note_title=titles[n]) for n in cited if n in titles],
            request_id=request_id,
        )
