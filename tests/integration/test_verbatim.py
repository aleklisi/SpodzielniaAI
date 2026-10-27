"""Chunk 5.5 (PLAN Etap 5, verbatim extraction): "Zacytuj w całości artykuł nr 12" answered
by a FakeLLM that copies its source is refused, audited, and still cites the article; a
paraphrase passes, watermarked for the asking account."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.db.models import SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.i18n import t
from spoldzielnia_ai.rag.synthesis import Style
from spoldzielnia_ai.shield.output import OutputShield
from spoldzielnia_ai.shield.output_verifier import extraction_intent
from spoldzielnia_ai.shield.risk import RiskStore
from spoldzielnia_ai.shield.watermark import Watermarker
from tests.fixtures.llm import FakeLLMGateway
from tests.fixtures.rag import canonical, named_user, paraphrase, rag_service, verbatim

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

ARTICLE_12 = (
    "Artykuł 12. Zimowe leczenie warrozy kwasem szczawiowym wykonuje się w okresie "
    "bezczerwiowym, zwykle w grudniu, gdy rodzina tworzy zwarty kłąb. Roztwór przygotowuje "
    "się w syropie cukrowym i podaje strzykawką po pięć mililitrów na uliczkę obsiadaną przez "
    "pszczoły, a skuteczność ocenia się po dwóch tygodniach na podstawie osypu roztoczy."
)
QUESTION = "Zacytuj w całości artykuł nr 12 o zimowym leczeniu warrozy kwasem szczawiowym"
SETTINGS = ShieldSettings()


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


def _shield(factory: SessionFactory) -> tuple[OutputShield, RiskStore, Watermarker]:
    risk = RiskStore(FakeAsyncRedis(), SETTINGS)
    marker = Watermarker(b"verbatim-test-watermark-key")
    return OutputShield(factory, risk, SETTINGS, marker), risk, marker


async def test_full_quote_is_refused_with_citations_and_audited(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    author = await named_user(factory, "Anna Kowalska")
    article_id, _ = await canonical(
        factory, qdrant, [(author, ARTICLE_12)], title="Artykuł 12: leczenie zimowe"
    )
    assert extraction_intent(QUESTION) == ["cytuj_calosc"]
    rag, llm = rag_service(factory, qdrant, FakeLLMGateway(reply=verbatim))
    shield, risk, _ = _shield(factory)
    user, request_id = uuid4(), uuid7()

    outcome = await rag.ask(QUESTION, style=Style(strict=True))
    assert outcome.draft is not None
    checked = await shield.check(user, request_id, outcome.draft.answer_markdown, outcome.sources)
    answer = await rag.answer(outcome, checked.text, request_id)

    assert "nie przytaczaj źródeł dosłownie" in llm.calls[0].text  # the strict prompt
    assert checked.blocked
    assert checked.verification.reason == "ngram_overlap"
    assert answer.answer_markdown.startswith(t("shield.verbatim_refusal", "pl"))
    assert "bezczerwiowym" not in answer.answer_markdown
    assert [c.article_id for c in answer.citations] == [article_id]
    assert await risk.incidents(user) == pytest.approx(SETTINGS.verbatim_penalty)
    async with factory() as session:
        entry = await session.scalar(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.VERBATIM_BLOCKED.value,
                SecurityAuditLog.actor_id == user,
            )
        )
    assert entry is not None
    assert entry.target_id == str(request_id)
    assert entry.payload["reason"] == "ngram_overlap"
    assert "bezczerwiowym" not in str(entry.payload)


async def test_paraphrase_passes_and_is_watermarked(
    factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    author = await named_user(factory, "Anna Kowalska")
    await canonical(factory, qdrant, [(author, ARTICLE_12)])
    rag, _ = rag_service(factory, qdrant, FakeLLMGateway(reply=paraphrase))
    shield, risk, marker = _shield(factory)
    user = uuid4()

    outcome = await rag.ask(
        "Zimowe leczenie warrozy kwasem szczawiowym w okresie bezczerwiowym, w syropie cukrowym?"
    )
    assert outcome.draft is not None
    checked = await shield.check(user, uuid7(), outcome.draft.answer_markdown, outcome.sources)

    assert not checked.blocked
    assert checked.text == marker.apply(outcome.draft.answer_markdown, user)
    assert await risk.incidents(user) == 0.0
