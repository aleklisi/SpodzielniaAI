"""Chunk 5.6 (PLAN Etap 5, distillation attack simulation).

An isolated database with a 200-block / 40-article canonical corpus. A bot walks the public
table of contents ("Opisz rozdział 1…50"), one chapter per query. The shield must cut it off
before it has pulled 15% of the blocks into synthesis, and must not lock a member who asks
50 narrow questions about their own two topics in the same run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.db.models import SecurityAuditLog, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.shield.challenge import HEADER as POW_HEADER
from spoldzielnia_ai.shield.challenge import solve
from spoldzielnia_ai.shield.coverage import CorpusCounter, CoverageTracker, Window
from tests.fixtures.corpus import ARTICLES, BLOCKS, Chapter, prepared_corpus, shield_settings
from tests.fixtures.governance import governance_harness
from tests.fixtures.rag import sources

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from httpx import Response
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn
    from tests.fixtures.llm import FakeLLMGateway
    from tests.integration.conftest import QdrantInstance

pytestmark = pytest.mark.integration

TOTAL = ARTICLES * BLOCKS
LIMIT = int(0.15 * TOTAL)  # 30 blocks


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


async def _ask(user: LoggedIn, question: str) -> Response:
    return await user.client.post("/rag/ask", json={"question": question}, headers=user.headers)


def _seen(llm: FakeLLMGateway) -> set[str]:
    return {text for call in llm.calls for _, text in sources(call)}


def _narrow_questions(a: Chapter, b: Chapter) -> list[str]:
    """A member working on one problem (sometimes a related one) asks in its own terms."""
    forms = [
        "Jak rozpoznać {x} i {y}?",
        "Czy {x} ma związek z {y} i {z}?",
        "{x} oraz {y}: co robić jesienią?",
        "Ile trwa {x} przy {y}?",
        "Jak {x} wpływa na {y} i {z}?",
    ]
    questions: list[str] = []
    for i in range(50):
        chapter = a if (i // 7) % 3 != 2 else b  # mostly one topic, sometimes the other
        t = chapter.terms
        questions.append(
            forms[i % len(forms)].format(x=t[i % 6], y=t[(i + 2) % 6], z=t[(i + 4) % 6])
        )
    return questions


async def _audit(factory: SessionFactory, user_id: str, event: EventType) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = await session.scalars(
            select(SecurityAuditLog)
            .where(
                SecurityAuditLog.actor_id == UUID(user_id),
                SecurityAuditLog.event_type == event.value,
            )
            .order_by(SecurityAuditLog.id)
        )
        return [dict(row.payload) for row in rows]


async def _is_locked(factory: SessionFactory, user_id: str) -> bool:
    async with factory() as session:
        user = await session.get(User, UUID(user_id))
        assert user is not None
        return user.is_locked


async def test_distillation_attack_is_cut_off_before_15_percent(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    corpus = await prepared_corpus(h, factory, qdrant)

    # --- The bot: walks the chapters, ignores challenges, keeps going until refused ---------
    bot_llm = corpus.llm()
    h.app.state.llm = bot_llm
    bot = await h.login()
    statuses: list[int] = []
    unique_when_cut: int | None = None
    for n in range(1, 51):
        response = await _ask(bot, corpus.walk(n))
        statuses.append(response.status_code)
        if response.status_code != 200 and unique_when_cut is None:
            unique_when_cut = len(_seen(bot_llm))
        if response.status_code in (403, 423):
            break

    assert unique_when_cut is not None, statuses
    assert unique_when_cut < LIMIT, (unique_when_cut, statuses)
    assert len(_seen(bot_llm)) < LIMIT  # nothing more after the cut
    state = h.app.state
    tracker = CoverageTracker(
        state.redis, CorpusCounter(state.redis, factory, 60), state.settings.shield
    )
    coverage = await tracker.check(UUID(bot.user_id))
    assert coverage.usage[Window.D7].blocks < LIMIT
    assert statuses[-1] == 403  # in the end: locked
    assert await _is_locked(factory, bot.user_id)
    decisions = await _audit(factory, bot.user_id, EventType.SHIELD_DECISION)
    assert any(d.get("level") == "CRITICAL" for d in decisions)
    assert (await bot.client.get("/me")).status_code == 423

    # --- A member asking about their own two topics, in the same run ------------------------
    member_llm = corpus.llm()
    h.app.state.llm = member_llm
    member = await h.login()
    questions = _narrow_questions(corpus.chapters[2], corpus.chapters[6])
    member_statuses = [(await _ask(member, q)).status_code for q in questions]

    assert member_statuses == [200] * 50
    assert not await _is_locked(factory, member.user_id)
    assert all(
        d["level"] != "CRITICAL" and "challenge" not in d
        for d in await _audit(factory, member.user_id, EventType.SHIELD_DECISION)
    )
    assert _seen(member_llm)  # answered from the corpus, not "no official position"


async def test_bot_paying_the_proof_of_work_ends_locked(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    """A bot that solves every challenge keeps getting answers at top_k 2, each puzzle 4x
    harder than the last, until coverage pressure takes it to CRITICAL. On this 200-block
    corpus the default lock floor (50 blocks, gap G14) is 25% of the corpus: such a bot pulls
    more than 15% before the lock. On corpora of 334+ blocks the 15% ratio governs instead."""
    corpus = await prepared_corpus(h, factory, qdrant)
    llm = corpus.llm()
    h.app.state.llm = llm
    bot = await h.login()
    difficulties: list[int] = []
    statuses: list[int] = []
    for n in range(1, 51):
        question = corpus.walk(n)
        response = await _ask(bot, question)
        if response.status_code == 401:
            puzzle = response.json()["detail"]["pow"]
            difficulties.append(puzzle["difficulty"])
            solution = f"{puzzle['nonce']}:{solve(puzzle['nonce'], puzzle['difficulty'])}"
            response = await bot.client.post(
                "/rag/ask",
                json={"question": question},
                headers={**bot.headers, POW_HEADER: solution},
            )
        statuses.append(response.status_code)
        if response.status_code == 403:
            break

    assert statuses[-1] == 403
    assert await _is_locked(factory, bot.user_id)
    floor = h.app.state.settings.shield.min_blocks_before_lock
    assert len(_seen(llm)) < floor
    assert difficulties == sorted(difficulties)
    assert difficulties[-1] > difficulties[0]
