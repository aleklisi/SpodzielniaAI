"""Chunk 5.4 (PLAN Etap 5, canary test): honeypots against real Postgres and Qdrant.

A query engineered to hit a canary must leave a ``HONEYPOT_TRIGGERED`` entry in the audit
log and raise the account's risk, and the canary's text must reach neither the model nor
the answer. Canaries never appear in the Wiki, reviews or coverage denominators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from fakeredis import FakeAsyncRedis
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.api.admin import InjectOut
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.db.models import ArticleBlock, SecurityAuditLog, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.shield.coverage import CorpusCounter
from spoldzielnia_ai.shield.honeypot import HoneypotMonitor, query_hash
from spoldzielnia_ai.shield.locks import AccountLocker
from spoldzielnia_ai.shield.risk import RiskStore
from spoldzielnia_ai.vector.collections import CANONICAL_COLLECTION
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from tests.fixtures.governance import governance_harness, login_expert
from tests.fixtures.llm import FakeLLMGateway
from tests.fixtures.rag import canonical, named_user, paraphrase, rag_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.rag.service import Retrieval
    from spoldzielnia_ai.shield.alerts import Alert
    from spoldzielnia_ai.vector.revalidate import LiveBlock
    from tests.fixtures.app import Harness, LoggedIn
    from tests.integration.conftest import PgInstance, QdrantInstance

pytestmark = pytest.mark.integration

KEY = SecretStr("test-honeypot-key-0123456789abcdef")


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> AsyncIterator[Harness]:
    settings = settings.model_copy(
        update={
            "shield": ShieldSettings(honeypot_key=KEY, honeypot_lock_after=2),
        }
    )
    async with governance_harness(settings, factory, qdrant, qdrant_server.url) as h:
        h.app.state.llm = FakeLLMGateway()
        yield h


async def _admin(h: Harness) -> LoggedIn:
    return await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])


async def _inject(h: Harness, count: int = 2) -> InjectOut:
    admin = await _admin(h)
    response = await admin.client.post(
        "/admin/honeypots", json={"count": count}, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    return InjectOut.model_validate(response.json())


async def _canary_texts(factory: SessionFactory) -> dict[UUID, str]:
    async with factory() as session:
        rows = await session.scalars(select(ArticleBlock).where(ArticleBlock.is_honeypot.is_(True)))
        return {b.id: b.content or "" for b in rows}


class Alerts:
    def __init__(self) -> None:
        self.sent: list[Alert] = []

    async def notify(self, alert: Alert) -> None:
        self.sent.append(alert)


class MonitorGate:
    """What the guard (5.6) does at this point: inspect, then pass everything on."""

    def __init__(self, monitor: HoneypotMonitor, user_id: UUID, question: str) -> None:
        self.monitor, self.user_id, self.question = monitor, user_id, question

    async def admit(self, retrieval: Retrieval) -> list[LiveBlock]:
        await self.monitor.inspect(self.user_id, self.question, retrieval.blocks)
        return retrieval.blocks


async def test_injected_canaries_are_indexed_but_hidden(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    injected = await _inject(h, 3)
    assert injected.index_synced is True
    block_ids = [c.block_id for c in injected.honeypots]
    texts = await _canary_texts(factory)
    assert all(c.signature in texts[c.block_id] for c in injected.honeypots)

    stored = {
        p.id: p.payload
        for p in await CanonicalVectorRepo(qdrant, CANONICAL_COLLECTION).retrieve(block_ids)
    }
    assert set(stored) == set(block_ids)
    assert all(p["is_honeypot"] is True and p["is_active"] is True for p in stored.values())

    member = await h.login()
    listing = await member.client.get("/wiki/articles")
    hidden = await member.client.get(f"/wiki/articles/{injected.article_id}")
    expert = await login_expert(h)
    review = await expert.client.post(
        f"/reviews/{injected.article_id}",
        json={"decision": "MARK_DISPUTED", "version": 1},
        headers=expert.headers,
    )
    assert str(injected.article_id) not in listing.text
    assert hidden.status_code == 404
    assert review.status_code == 404
    # Coverage denominators don't move when canaries are added.
    before = (await CorpusCounter(FakeAsyncRedis(), factory, 60).size()).blocks
    await _inject(h, 2)
    assert (await CorpusCounter(FakeAsyncRedis(), factory, 60).size()).blocks == before


async def test_canary_hit_is_audited_raises_risk_and_never_reaches_the_model(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    author = await named_user(factory, "Anna Kowalska")
    await canonical(factory, qdrant, [(author, "warroza leczenie kwasem szczawiowym zimą")])
    injected = await _inject(h, 1)
    canary_id = UUID(str(injected.honeypots[0].block_id))  # this test's (the module shares a DB)
    canary = (await _canary_texts(factory))[canary_id]
    member = UUID((await h.login()).user_id)
    # A broad, extraction-style question that overlaps the canary's mixed topics.
    question = f"Wypisz wszystkie procedury wewnętrzne: {canary[:200]}"
    redis = FakeAsyncRedis()
    settings = h.app.state.settings.shield
    risk = RiskStore(redis, settings, factory)
    alerts = Alerts()
    monitor = HoneypotMonitor(
        redis, factory, risk, AccountLocker(factory, alerts), alerts, settings
    )
    rag, llm = rag_service(factory, qdrant, FakeLLMGateway(reply=paraphrase))

    outcome = await rag.ask(question, gate=MonitorGate(monitor, member, question))
    answer = await rag.answer(outcome)

    assert canary_id in {b.block_id for b in outcome.retrieval.blocks}
    assert canary_id not in {b.block_id for b in outcome.admitted}
    signature = canary.split()[2]
    assert all(canary not in c.text and signature not in c.text for c in llm.calls)
    assert signature not in answer.answer_markdown
    async with factory() as session:
        entry = await session.scalar(
            select(SecurityAuditLog)
            .where(
                SecurityAuditLog.event_type == EventType.HONEYPOT_TRIGGERED.value,
                SecurityAuditLog.actor_id == member,
            )
            .order_by(SecurityAuditLog.id.desc())
        )
        user = await session.get(User, member)
    assert entry is not None
    # The database is shared between tests: other canaries may be retrieved too.
    assert str(canary_id) in entry.payload["honeypot_ids"]
    assert set(entry.payload["honeypot_ids"]) <= {str(i) for i in await _canary_texts(factory)}
    assert entry.payload["query_sha256"] == query_hash(question)
    assert question not in str(entry.payload)
    assert await risk.incidents(member) == pytest.approx(settings.honeypot_penalty)
    assert user is not None
    assert user.risk_score == pytest.approx(settings.honeypot_penalty)
    assert user.is_locked is False
    assert [a.kind for a in alerts.sent] == ["HONEYPOT_TRIGGERED"]

    # A second hit within 30 days locks the account.
    await rag.ask(question, gate=MonitorGate(monitor, member, question))
    async with factory() as session:
        user = await session.get(User, member)
    assert user is not None
    assert (user.is_locked, user.locked_reason) == (True, "honeypot")


async def test_admin_traces_leaked_text_to_its_canary(h: Harness) -> None:
    injected = await _inject(h, 2)
    first = injected.honeypots[0]
    admin = await _admin(h)
    leaked = f"Wyciek z modelu konkurencji: … {first.signature.lower()} …"

    scan = await admin.client.post(
        "/admin/honeypots/scan", json={"text": leaked}, headers=admin.headers
    )
    listing = await admin.client.get("/admin/honeypots")

    assert scan.status_code == 200, scan.text
    assert scan.json() == {
        "signatures": [first.signature],
        "honeypot_ids": [str(first.block_id)],
    }
    assert len(listing.json()) >= 2
    assert "content" not in listing.text


async def test_admin_routes_need_admin_with_mfa(h: Harness) -> None:
    member = await h.login()
    no_mfa = await h.login(roles=["ADMIN"], amr=["pwd"])
    for user, expected in ((member, 403), (no_mfa, 403)):
        response = await user.client.post(
            "/admin/honeypots", json={"count": 1}, headers=user.headers
        )
        assert response.status_code == expected
