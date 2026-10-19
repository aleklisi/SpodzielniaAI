"""Chunk 4.2: expert review through the API, against real Postgres and Qdrant.

The key guarantee: when a review response is sent, an article that left CANONICAL
(superseded, disputed) is no longer returned by canonical search.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.settings import GovernanceSettings
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import SecurityAuditLog, WikiArticle
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
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

OLD = "warroza leczenie amitrazą jesienią dawka"
NEW = "warroza leczenie kwasem szczawiowym zimą sublimacja"


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


async def _status(factory: SessionFactory, article_id: UUID) -> WikiArticle:
    async with factory() as session:
        article = await session.get(WikiArticle, article_id)
        assert article is not None
        return article


async def _hits(qdrant: AsyncQdrantClient, text: str) -> set[UUID]:
    return {h.block_id for h in await CanonicalVectorRepo(qdrant).search(hash_embedding(text), 50)}


async def _submit(user: LoggedIn, article_id: UUID) -> int:
    response = await user.client.post(f"/wiki/articles/{article_id}/submit", headers=user.headers)
    return response.status_code


async def test_draft_to_canonical_is_indexed_and_audited(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice = await h.login()
    expert = await login_expert(h)
    article_id, blocks = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.DRAFT
    )

    assert await _submit(alice, article_id) == 200
    queue = (await expert.client.get("/reviews/queue")).json()
    assert [(i["status"], i["is_own"]) for i in queue if i["article_id"] == str(article_id)] == [
        ("IN_REVIEW", False)
    ]
    result = await approve(expert, article_id)

    assert (result["previous"], result["status"], result["index_synced"]) == (
        "IN_REVIEW",
        "CANONICAL",
        True,
    )
    assert await _hits(qdrant, NEW) == set(blocks)
    article = await _status(factory, article_id)
    assert article.last_verified_at is not None
    listed = {a["id"]: a for a in (await alice.client.get("/wiki/articles")).json()}
    assert [a["id"] for a in listed[str(article_id)]["authors"]] == [alice.user_id]
    async with factory() as session:
        events = list(
            await session.scalars(
                select(SecurityAuditLog)
                .where(SecurityAuditLog.target_id == str(article_id))
                .order_by(SecurityAuditLog.id)
            )
        )
    assert [(e.event_type, e.payload["to"]) for e in events] == [
        ("ARTICLE_STATUS_CHANGED", "IN_REVIEW"),
        ("ARTICLE_STATUS_CHANGED", "CANONICAL"),
    ]
    assert events[1].actor_id == UUID(expert.user_id)


async def test_approve_with_supersede_removes_old_vectors_immediately(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice = await h.login()
    expert = await login_expert(h)
    old_id, old_blocks = await make_article(factory, [(UUID(alice.user_id), OLD)])
    await h.app.state.processing_queue.article_canonical(old_id)  # indexed earlier
    assert await _hits(qdrant, OLD) == set(old_blocks)
    new_id, new_blocks = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.IN_REVIEW
    )

    result = await approve(expert, new_id, supersedes_id=str(old_id))

    # Immediately after the response: the old article's points are out of canonical search.
    assert result["status"] == "CANONICAL"
    hits = await _hits(qdrant, OLD) | await _hits(qdrant, NEW)
    assert hits == set(new_blocks)
    old, new = await _status(factory, old_id), await _status(factory, new_id)
    assert (old.status, old.superseded_by_id) == (ArticleStatus.SUPERSEDED, new_id)
    assert (new.status, new.supersedes_id) == (ArticleStatus.CANONICAL, old_id)
    stored = await CanonicalVectorRepo(qdrant).retrieve(old_blocks)
    assert [p.payload["article_status"] for p in stored] == ["SUPERSEDED"]
    # Members can still read the superseded article and follow the link.
    view = (await alice.client.get(f"/wiki/articles/{old_id}")).json()
    assert view["superseded_by_id"] == str(new_id)


async def test_supersede_requires_a_canonical_target(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    expert = await login_expert(h)
    draft_id, _ = await make_article(factory, [(UUID(alice.user_id), OLD)], ArticleStatus.DRAFT)
    new_id, _ = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.IN_REVIEW
    )

    response = await expert.client.post(
        f"/reviews/{new_id}",
        json={"decision": "APPROVE", "version": 1, "supersedes_id": str(draft_id)},
        headers=expert.headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "supersedes_not_canonical"
    assert (await _status(factory, new_id)).status == ArticleStatus.IN_REVIEW


async def test_disputed_leaves_retrieval_and_reapproval_restores_it(
    h: Harness, factory: SessionFactory, qdrant: AsyncQdrantClient
) -> None:
    alice = await h.login()
    expert = await login_expert(h)
    article_id, blocks = await make_article(factory, [(UUID(alice.user_id), OLD)])
    await h.app.state.processing_queue.article_canonical(article_id)
    version = (await _status(factory, article_id)).version

    disputed = await expert.client.post(
        f"/reviews/{article_id}",
        json={"decision": "MARK_DISPUTED", "version": version, "comment": "nowe badania"},
        headers=expert.headers,
    )

    assert disputed.json()["status"] == "DISPUTED"
    assert await _hits(qdrant, OLD) == set()
    assert (await alice.client.get(f"/wiki/articles/{article_id}")).status_code == 200  # author
    other = await h.login()
    assert (await other.client.get(f"/wiki/articles/{article_id}")).status_code == 404

    await approve(expert, article_id)
    assert await _hits(qdrant, OLD) == set(blocks)


async def test_reviewer_cannot_decide_on_own_article(h: Harness, factory: SessionFactory) -> None:
    expert = await login_expert(h)
    alice = await h.login()
    article_id, _ = await make_article(
        factory,
        [(UUID(alice.user_id), NEW), (UUID(expert.user_id), OLD)],
        status=ArticleStatus.IN_REVIEW,
    )
    queue = (await expert.client.get("/reviews/queue")).json()
    assert [i["is_own"] for i in queue if i["article_id"] == str(article_id)] == [True]

    response = await expert.client.post(
        f"/reviews/{article_id}",
        json={"decision": "APPROVE", "version": 1},
        headers=expert.headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "own_article"
    assert (await _status(factory, article_id)).status == ArticleStatus.IN_REVIEW


async def test_reviews_need_expert_role_and_mfa(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    article_id, _ = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.IN_REVIEW
    )
    no_mfa = await login_expert(h, mfa=False)
    member = await h.login()

    for user in (no_mfa, member):
        response = await user.client.post(
            f"/reviews/{article_id}",
            json={"decision": "APPROVE", "version": 1},
            headers=user.headers,
        )
        assert response.status_code == 403
    assert (await no_mfa.client.get("/reviews/queue")).json()["detail"]["error"] == "mfa_required"
    assert (await _status(factory, article_id)).status == ArticleStatus.IN_REVIEW


async def test_stale_version_is_rejected(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    expert = await login_expert(h)
    article_id, _ = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.DRAFT
    )
    stale = (await _status(factory, article_id)).version
    assert await _submit(alice, article_id) == 200  # bumps the version

    response = await expert.client.post(
        f"/reviews/{article_id}",
        json={"decision": "APPROVE", "version": stale},
        headers=expert.headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "version_conflict"


async def test_quorum_needs_distinct_experts(
    settings: Settings,
    factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    qdrant_server: QdrantInstance,
) -> None:
    two = settings.model_copy(update={"governance": GovernanceSettings(review_quorum=2)})
    async with governance_harness(two, factory, qdrant, qdrant_server.url) as h:
        alice = await h.login()
        first, second = await login_expert(h), await login_expert(h)
        article_id, _ = await make_article(
            factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.IN_REVIEW
        )

        once = await approve(first, article_id)
        again = await approve(first, article_id)  # the same expert again doesn't count twice
        assert (once["status"], once["approvals"], once["quorum"]) == ("IN_REVIEW", 1, 2)
        assert (again["status"], again["approvals"]) == ("IN_REVIEW", 1)

        done = await approve(second, article_id)
        assert (done["status"], done["approvals"]) == ("CANONICAL", 2)


async def test_drafts_are_private_to_authors_and_reviewers(
    h: Harness, factory: SessionFactory
) -> None:
    alice = await h.login()
    other = await h.login()
    expert = await login_expert(h)
    article_id, _ = await make_article(
        factory, [(UUID(alice.user_id), NEW)], status=ArticleStatus.DRAFT
    )

    assert (await alice.client.get(f"/wiki/articles/{article_id}")).status_code == 200
    assert (await expert.client.get(f"/wiki/articles/{article_id}")).status_code == 200
    assert (await other.client.get(f"/wiki/articles/{article_id}")).status_code == 404
    assert await _submit(other, article_id) == 404
    listed = (await other.client.get("/wiki/articles")).json()
    assert str(article_id) not in {a["id"] for a in listed}


async def test_withheld_blocks_in_the_wiki_view(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    bob = await h.login()
    article_id, blocks = await make_article(
        factory,
        [(UUID(alice.user_id), NEW), (UUID(bob.user_id), OLD)],
        consents=[ConsentStatus.ACTIVE, ConsentStatus.SUSPENDED],
    )

    seen_by_alice = (await alice.client.get(f"/wiki/articles/{article_id}")).json()["blocks"]
    seen_by_bob = (await bob.client.get(f"/wiki/articles/{article_id}")).json()["blocks"]

    assert seen_by_alice[0]["content"] == NEW
    assert seen_by_alice[1] | {"content": None, "withheld": True} == seen_by_alice[1]
    assert seen_by_alice[1]["notice"] == "Treść wstrzymana przez autora"
    assert seen_by_bob[1]["content"] == OLD  # authors still see their own suspended text
    assert [b["id"] for b in seen_by_alice] == [str(b) for b in blocks]
