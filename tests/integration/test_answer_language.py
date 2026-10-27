"""Chunk 14.5: the cooperative chat answers in the asker's UI language, through the same shield.

An English member's question reaches the model with "answer in English"; the server's own texts
are English; a translated source is refused (in English) and audited as ``translation``; and when
the cross-language check cannot run, the answer is written again in Polish, checked by the
word-level verifier, with a note in English. The notebook's Q&A follows the language too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.db.models import SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.i18n import t
from spoldzielnia_ai.llm.prompts import LANGUAGE_RULES
from spoldzielnia_ai.shield.translation import (
    TranslationCheck,
    TranslationResult,
    TranslationUncheckable,
)
from tests.fixtures.corpus import Corpus, prepared_corpus, shield_settings
from tests.fixtures.governance import governance_harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from httpx import Response
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn
    from tests.fixtures.llm import FakeLLMGateway
    from tests.integration.conftest import QdrantInstance

pytestmark = pytest.mark.integration

EN = LANGUAGE_RULES["en"].strip()
PL = LANGUAGE_RULES["pl"].strip()


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> AsyncIterator[Harness]:
    async with governance_harness(
        shield_settings(settings), factory, qdrant, qdrant_server.url
    ) as h:
        yield h


@pytest.fixture
async def corpus(h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient) -> Corpus:
    return await prepared_corpus(h, factory, qdrant)


@pytest.fixture
def llm(h: Harness, corpus: Corpus) -> FakeLLMGateway:
    llm = corpus.llm()
    h.app.state.llm = llm
    return llm


async def _english(h: Harness) -> LoggedIn:
    member = await h.login()
    response = await member.client.put("/me/locale", json={"locale": "en"}, headers=member.headers)
    assert response.status_code == 204
    return member


async def _ask(user: LoggedIn, question: str) -> Response:
    return await user.client.post("/rag/ask", json={"question": question}, headers=user.headers)


async def test_an_english_member_gets_an_english_answer(
    h: Harness, corpus: Corpus, llm: FakeLLMGateway
) -> None:
    member = await _english(h)
    response = await _ask(member, corpus.walk(1))

    assert response.status_code == 200, response.text
    assert EN in llm.calls[-1].text
    assert PL not in llm.calls[-1].text
    assert response.json()["citations"]


async def test_a_polish_member_is_unchanged(
    h: Harness, corpus: Corpus, llm: FakeLLMGateway
) -> None:
    member = await h.login()
    response = await _ask(member, corpus.walk(1))
    assert response.status_code == 200, response.text
    assert PL in llm.calls[-1].text


async def test_no_official_position_in_english(h: Harness, llm: FakeLLMGateway) -> None:
    member = await _english(h)
    response = await _ask(member, "Jak hodować alpaki na balkonie w bloku?")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["no_official_position"] is True
    assert body["answer_markdown"] == t("rag.no_official_position", "en")


async def test_a_translated_source_is_refused_in_english_and_audited(
    h: Harness,
    corpus: Corpus,
    llm: FakeLLMGateway,
    factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def translated(self: TranslationCheck, answer: str, sources: Sequence[str]) -> object:
        return TranslationResult(True, 0.9, 40)

    monkeypatch.setattr(TranslationCheck, "check", translated)
    member = await _english(h)
    response = await _ask(member, corpus.walk(1))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_markdown"].startswith(t("shield.verbatim_refusal", "en"))
    assert body["citations"]  # the articles are still cited: read them in the Wiki
    async with factory() as session:
        entry = await session.scalar(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.VERBATIM_BLOCKED.value,
                SecurityAuditLog.actor_id == UUID(member.user_id),
            )
        )
    assert entry is not None
    assert entry.payload["reason"] == "translation"
    assert entry.payload["xlang_share"] == 0.9


async def test_an_uncheckable_english_answer_is_given_in_polish(
    h: Harness, corpus: Corpus, llm: FakeLLMGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(self: TranslationCheck, answer: str, sources: Sequence[str]) -> object:
        raise TranslationUncheckable("no embedder")

    monkeypatch.setattr(TranslationCheck, "check", down)
    member = await _english(h)
    before = len(llm.calls)
    response = await _ask(member, corpus.walk(1))

    assert response.status_code == 200, response.text
    drafts = llm.calls[before:]
    assert EN in drafts[-2].text  # the English draft, which could not be checked
    assert PL in drafts[-1].text  # written again in Polish
    body = response.json()
    assert body["answer_markdown"].startswith(t("rag.answered_in_polish", "en"))
    assert body["citations"]


async def test_the_notebook_answers_in_the_members_language(
    h: Harness, llm: FakeLLMGateway
) -> None:
    member = await _english(h)
    response = await member.client.post(
        "/notes/ask", json={"question": "Co zapisałem o zimowli?"}, headers=member.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["answer_markdown"] == t("rag.no_notes_answer", "en")
