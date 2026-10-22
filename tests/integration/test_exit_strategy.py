"""Chunk 4.3 (PLAN Etap 4 verification): Exit Strategy against real Postgres and Qdrant.

``test_coauthor_revoke_cascade`` is the plan's test: right after the revoke response, neither
the revoked co-author's block nor any other block of the article is retrievable.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, SecurityAuditLog, WikiArticle
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.vector.collections import CANONICAL_COLLECTION
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.wiki.consent import REVISION_ALERT
from tests.fixtures.governance import approve, governance_harness, login_expert
from tests.fixtures.llm import hash_embedding
from tests.fixtures.wiki import make_article

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn
    from tests.integration.conftest import PgInstance, QdrantInstance

pytestmark = pytest.mark.integration

TEXT_A = "warroza leczenie kwasem szczawiowym zimą w okresie bezczerwiowym"
TEXT_B = "warroza monitoring osypu na wkładce dennej przez tydzień"
REVOKE = {"confirm": "REVOKE"}


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
    async with governance_harness(settings, factory, qdrant, qdrant_server.url) as h:
        yield h


async def _canonical(
    h: Harness, factory: SessionFactory, blocks: list[tuple[LoggedIn, str]]
) -> tuple[UUID, list[UUID]]:
    """An article approved by an expert (and so indexed)."""
    article_id, block_ids = await make_article(
        factory, [(UUID(u.user_id), t) for u, t in blocks], status=ArticleStatus.IN_REVIEW
    )
    await approve(await login_expert(h), article_id)
    return article_id, block_ids


async def _hits(qdrant: AsyncQdrantClient, text: str) -> set[UUID]:
    return {
        h.block_id
        for h in await CanonicalVectorRepo(qdrant, CANONICAL_COLLECTION).search(
            hash_embedding(text), 50
        )
    }


async def _article(factory: SessionFactory, article_id: UUID) -> WikiArticle:
    async with factory() as session:
        article = await session.get(WikiArticle, article_id)
        assert article is not None
        return article


async def _blocks(factory: SessionFactory, ids: list[UUID]) -> list[ArticleBlock]:
    async with factory() as session:
        rows = await session.scalars(
            select(ArticleBlock).where(ArticleBlock.id.in_(ids)).order_by(ArticleBlock.position)
        )
        return list(rows)


async def _post(user: LoggedIn, action: str, **body: object) -> dict[str, object]:
    response = await user.client.post(
        f"/me/contributions/{action}", json=body, headers=user.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_coauthor_revoke_cascade(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await h.login(), await h.login()
    article_id, (a_block, b_block) = await _canonical(h, factory, [(alice, TEXT_A), (bob, TEXT_B)])
    assert await _hits(qdrant, TEXT_B) == {a_block, b_block}

    started = time.perf_counter()
    result = await _post(bob, "revoke", **REVOKE)
    # Immediately after the response, nothing of the article is retrievable.
    hits = await _hits(qdrant, TEXT_B) | await _hits(qdrant, TEXT_A)
    propagation = time.perf_counter() - started

    assert hits.isdisjoint({a_block, b_block})
    assert propagation < 1.0, f"consent propagation took {propagation:.3f}s"
    assert result["index_synced"] is True
    assert (result["blocks_affected"], result["degraded"]) == (1, [str(article_id)])

    article = await _article(factory, article_id)
    assert (article.status, article.review_alert) == (
        ArticleStatus.REVISION_REQUIRED,
        REVISION_ALERT,
    )
    a_row, b_row = await _blocks(factory, [a_block, b_block])
    assert (b_row.consent_status, b_row.content, b_row.content_hash) == (
        ConsentStatus.REVOKED,
        None,
        None,
    )
    assert (a_row.consent_status, a_row.content) == (ConsentStatus.ACTIVE, TEXT_A)
    repo = CanonicalVectorRepo(qdrant, CANONICAL_COLLECTION)
    assert await repo.retrieve([b_block]) == []  # hard-deleted
    (a_point,) = await repo.retrieve([a_block])
    assert (a_point.payload["is_active"], a_point.payload["article_status"]) == (
        False,
        "REVISION_REQUIRED",
    )

    expert = await login_expert(h)
    queue = (await expert.client.get("/reviews/queue")).json()
    item = next(i for i in queue if i["article_id"] == str(article_id))
    assert (item["status"], item["review_alert"]) == ("REVISION_REQUIRED", REVISION_ALERT)

    async with factory() as session:
        events = list(
            await session.scalars(
                select(SecurityAuditLog)
                .where(SecurityAuditLog.actor_id == UUID(bob.user_id))
                .where(SecurityAuditLog.event_type.notin_(["USER_LOGIN", "USER_PROVISIONED"]))
                .order_by(SecurityAuditLog.id)
            )
        )
    assert [e.event_type for e in events] == [
        "ARTICLE_STATUS_CHANGED",
        "ARTICLE_DEGRADED",
        "CONSENT_REVOKED",
    ]
    assert events[-1].payload | {"blocks": 1, "points": 1, "degraded": 1} == events[-1].payload


async def test_suspend_then_resume_restores_retrieval(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice, reader = await h.login(), await h.login()
    article_id, (block,) = await _canonical(h, factory, [(alice, TEXT_A)])

    suspended = await _post(alice, "suspend")
    assert suspended["degraded"] == []  # a single-author article stays canonical
    assert await _hits(qdrant, TEXT_A) == set()
    view = (await reader.client.get(f"/wiki/articles/{article_id}")).json()
    assert view["blocks"][0] | {"withheld": True, "content": None} == view["blocks"][0]

    resumed = await _post(alice, "resume")
    assert resumed["blocks_affected"] == 1
    assert await _hits(qdrant, TEXT_A) == {block}
    assert (await _article(factory, article_id)).status == ArticleStatus.CANONICAL


async def test_resume_does_not_reactivate_a_degraded_article(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await h.login(), await h.login()
    article_id, blocks = await _canonical(h, factory, [(alice, TEXT_A), (bob, TEXT_B)])

    assert (await _post(bob, "suspend"))["degraded"] == [str(article_id)]
    await _post(bob, "resume")

    assert await _hits(qdrant, TEXT_A) | await _hits(qdrant, TEXT_B) == set()
    rows = await _blocks(factory, blocks)
    assert [r.consent_status for r in rows] == [ConsentStatus.ACTIVE, ConsentStatus.ACTIVE]
    assert (await _article(factory, article_id)).status == ArticleStatus.REVISION_REQUIRED

    # Back through review: resubmitted by its author, approved, re-indexed.
    submitted = await alice.client.post(
        f"/wiki/articles/{article_id}/submit", headers=alice.headers
    )
    assert submitted.json()["status"] == "IN_REVIEW"
    await approve(await login_expert(h), article_id)
    assert await _hits(qdrant, TEXT_B) == set(blocks)


async def test_members_act_only_on_their_own_blocks(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice, bob = await h.login(), await h.login()
    bobs_article, bobs_blocks = await _canonical(h, factory, [(bob, TEXT_B)])
    await _canonical(h, factory, [(alice, TEXT_A)])

    targeted = await _post(alice, "suspend", article_ids=[str(bobs_article)])
    revoked = await _post(alice, "revoke", article_ids=[str(bobs_article)], **REVOKE)
    everything = await _post(alice, "suspend")

    assert (targeted["blocks_affected"], revoked["blocks_affected"]) == (0, 0)
    assert everything["articles"] != [str(bobs_article)]
    assert [r.consent_status for r in await _blocks(factory, bobs_blocks)] == [ConsentStatus.ACTIVE]
    assert await _hits(qdrant, TEXT_B) == set(bobs_blocks)
    mine = (await alice.client.get("/me/contributions")).json()
    assert str(bobs_article) not in {c["article_id"] for c in mine}


async def test_single_author_revoke_withdraws_the_article(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice = await h.login()
    article_id, _ = await _canonical(h, factory, [(alice, TEXT_A)])

    result = await _post(alice, "revoke", article_ids=[str(article_id)], **REVOKE)

    assert (result["withdrawn"], result["degraded"]) == ([str(article_id)], [])
    assert (await _article(factory, article_id)).status == ArticleStatus.REJECTED
    listed = (await alice.client.get("/wiki/articles")).json()
    assert str(article_id) not in {a["id"] for a in listed}
    assert await _hits(qdrant, TEXT_A) == set()
    (contribution,) = [
        c
        for c in (await alice.client.get("/me/contributions")).json()
        if c["article_id"] == str(article_id)
    ]
    assert [b["consent_status"] for b in contribution["blocks"]] == ["REVOKED"]


async def test_revoke_needs_confirmation_and_a_recent_login(
    h: Harness, factory: SessionFactory
) -> None:
    stale = await h.login(auth_time=int(time.time()) - 3600)
    await make_article(factory, [(UUID(stale.user_id), TEXT_A)])

    unconfirmed = await stale.client.post(
        "/me/contributions/revoke", json={}, headers=stale.headers
    )
    old_login = await stale.client.post(
        "/me/contributions/revoke", json=REVOKE, headers=stale.headers
    )

    assert unconfirmed.status_code == 422
    assert old_login.status_code == 403
    assert old_login.json()["detail"]["error"] == "reauthentication_required"
    assert "prompt=login" in old_login.json()["detail"]["step_up"]
    # Suspending (reversible) needs no fresh login.
    assert (await _post(stale, "suspend"))["blocks_affected"] == 1


async def test_database_keeps_revocation_final_and_contentless(
    h: Harness, factory: SessionFactory
) -> None:
    alice = await h.login()
    _, (block,) = await make_article(factory, [(UUID(alice.user_id), TEXT_A)])
    await _post(alice, "revoke", **REVOKE)

    async with factory() as session:
        with pytest.raises(DBAPIError, match="revoked consent is final"):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE article_block SET consent_status = 'ACTIVE', content = 'x', "
                        "content_hash = 'x' WHERE id = :id"
                    ),
                    {"id": block},
                )
    _, (other,) = await make_article(factory, [(UUID(alice.user_id), TEXT_B)])
    async with factory() as session:
        with pytest.raises(DBAPIError, match="revoked_is_empty"):
            async with session.begin():
                await session.execute(
                    text("UPDATE article_block SET consent_status = 'REVOKED' WHERE id = :id"),
                    {"id": other},
                )


async def test_history_lists_only_the_members_own_consent_changes(
    h: Harness, factory: SessionFactory
) -> None:
    alice, bob = await h.login(), await h.login()
    article_id, _ = await _canonical(h, factory, [(alice, TEXT_A), (bob, TEXT_B)])
    await _post(bob, "suspend")
    await _post(bob, "resume")
    await _post(bob, "revoke", **REVOKE)
    await _post(alice, "suspend", article_ids=[str(article_id)])

    mine = (await bob.client.get("/me/contributions/history")).json()
    theirs = (await alice.client.get("/me/contributions/history")).json()

    # Bob's suspension already sent the co-authored article back to review (recorded just
    # before the suspension itself); by the time he revokes, it is no longer canonical.
    assert [e["event"] for e in mine] == ["REVOKE", "RESUME", "SUSPEND", "ARTICLE_DEGRADED"]
    revoke, degraded = mine[0], mine[3]
    assert (revoke["blocks"], revoke["articles"], revoke["degraded"], revoke["scope"]) == (
        1,
        1,
        0,
        "all",
    )
    assert degraded["article_id"] == str(article_id)
    assert degraded["blocks"] == 1
    assert [e["event"] for e in theirs] == ["SUSPEND"]
    assert theirs[0]["scope"] == "selected"
