"""Strict-grounding synthesis shared by cooperative RAG and notebook Q&A (5.1).

Sources reach the model as untrusted data labelled ``S1…Sn`` (3.4 builder). A reply counts
only if it cites at least one source and every ``[Sx]`` marker (and every entry of
``citations``) is one of the labels it was given. A reply that lists valid labels in
``citations`` but has no inline markers gets them appended (``attach_listed_citations``).
Otherwise the model gets one corrective retry, then the caller falls back to "no answer". It
never answers from general knowledge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.llm.gateway import LLMOutputInvalid
from spoldzielnia_ai.llm.prompts import LANGUAGE_RULES, STRICT_RULE
from spoldzielnia_ai.rag.schemas import AnswerDraft

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from spoldzielnia_ai.i18n import Locale
    from spoldzielnia_ai.llm.gateway import ChatMessage, LLMGateway, LLMPurpose
    from spoldzielnia_ai.llm.prompts import PromptTemplate

log = get_logger(__name__)

_GROUP = re.compile(r"\[\s*(S\d{1,3}(?:\s*[,;]\s*S\d{1,3})+)\s*\]")
_MARKER = re.compile(r"\[(S\d{1,3})\]")
_RUN = re.compile(r"\[\d+\](?:\s*\[\d+\])+")

CORRECTION = (
    "Odpowiedź nie spełnia zasad: każde twierdzenie musi mieć cytowanie [Sx], a wolno używać "
    "wyłącznie etykiet podanych źródeł: {labels}. Popraw odpowiedź albo ustaw insufficient na "
    "true, jeśli źródła nie odpowiadają na pytanie."
)


@dataclass(frozen=True, slots=True)
class Source:
    """One retrieved passage. ``group`` is the citable unit (article, or note)."""

    label: str
    text: str
    group: UUID


@dataclass(frozen=True, slots=True)
class Style:
    strict: bool = False  # extraction intent (5.5): never quote
    max_words: int = 350
    language: Locale = "pl"  # the asker's UI language (14.5)


def label_sources(texts: Sequence[tuple[str, UUID]]) -> list[Source]:
    return [Source(f"S{i}", text, group) for i, (text, group) in enumerate(texts, start=1)]


def normalize_markers(text: str) -> str:
    """``[S1, S2]`` → ``[S1][S2]``."""
    return _GROUP.sub(
        lambda m: "".join(f"[{p.strip()}]" for p in re.split(r"[,;]", m.group(1))), text
    )


def markers(text: str) -> list[str]:
    return _MARKER.findall(text)


def is_grounded(draft: AnswerDraft, labels: set[str]) -> bool:
    used = markers(draft.answer_markdown)
    return bool(used) and set(used) <= labels and set(draft.citations) <= labels


def attach_listed_citations(draft: AnswerDraft, labels: set[str]) -> AnswerDraft:
    """Some local models list their sources in ``citations`` but write no inline ``[Sx]``.
    If every listed label is a real source, put the markers at the end of the answer: the
    citation is coarser, never invented."""
    if markers(draft.answer_markdown) or not draft.citations:
        return draft
    if not set(draft.citations) <= labels:
        return draft
    tail = "".join(f"[{label}]" for label in dict.fromkeys(draft.citations))
    return draft.model_copy(update={"answer_markdown": f"{draft.answer_markdown.rstrip()} {tail}"})


def renumber(text: str, sources: Sequence[Source]) -> tuple[str, list[UUID]]:
    """Replace ``[Sx]`` with ``[n]``: n is the position of the source's group among the groups
    cited, in order of first citation. The client never learns how many passages an article
    contributed. Returns the text and the cited groups."""
    group_of = {s.label: s.group for s in sources}
    order: list[UUID] = []

    def number(match: re.Match[str]) -> str:
        group = group_of.get(match.group(1))
        if group is None:
            return ""
        if group not in order:
            order.append(group)
        return f"[{order.index(group) + 1}]"

    def dedupe(run: re.Match[str]) -> str:
        # Passages of one article share its number: "[1][2][1]" reads "[1][2]".
        return "".join(dict.fromkeys(re.findall(r"\[\d+\]", run.group(0))))

    return _RUN.sub(dedupe, _MARKER.sub(number, text)), order


class Synthesizer:
    def __init__(
        self,
        gateway: LLMGateway,
        template: PromptTemplate,
        purpose: LLMPurpose,
        *,
        private_data: bool,
    ) -> None:
        self._gateway = gateway
        self._template = template
        self._purpose = purpose
        self._force_local = private_data

    def messages(self, question: str, sources: Sequence[Source], style: Style) -> list[ChatMessage]:
        messages, _ = self._template.messages(
            [(s.label, s.text) for s in sources],
            question=question,
            max_words=str(style.max_words),
            language_rule=LANGUAGE_RULES[style.language],
            strict_rule=STRICT_RULE if style.strict else "",
        )
        return messages

    async def draft(
        self, question: str, sources: Sequence[Source], style: Style
    ) -> AnswerDraft | None:
        """A grounded draft, or None (insufficient sources, ungrounded twice, invalid output).
        ``ModelUnavailable`` propagates: that is an outage, not an answer."""
        if not sources:
            return None
        labels = {s.label for s in sources}
        messages = self.messages(question, sources, style)
        for attempt in (1, 2):
            try:
                result = await self._gateway.complete(
                    messages,
                    purpose=self._purpose,
                    private_data=self._force_local,
                    response_model=AnswerDraft,
                )
            except LLMOutputInvalid:
                log.warning("rag.invalid_output", template=self._template.name)
                return None
            draft = result.parsed
            if draft is None or draft.insufficient:
                return None
            draft = draft.model_copy(
                update={"answer_markdown": normalize_markers(draft.answer_markdown)}
            )
            draft = attach_listed_citations(draft, labels)
            if is_grounded(draft, labels):
                return draft
            log.warning("rag.ungrounded", template=self._template.name, attempt=attempt)
            correction: list[ChatMessage] = [
                {"role": "assistant", "content": result.content},
                {"role": "user", "content": CORRECTION.format(labels=", ".join(sorted(labels)))},
            ]
            messages = [*messages, *correction]
        return None
