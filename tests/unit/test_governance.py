"""Chunk 4.2: the review state machine (exhaustively) and the vector operations it produces."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from spoldzielnia_ai.db.enums import ArticleStatus, ReviewDecision
from spoldzielnia_ai.vector.repositories import CanonicalScope
from spoldzielnia_ai.vector.sync import (
    OpKind,
    VectorOp,
    VectorSync,
    deactivate_articles,
    delete_blocks,
    reindex_articles,
    resume_blocks,
    suspend_blocks,
)
from spoldzielnia_ai.wiki.governance import (
    ACTORS,
    DECISION_ACTION,
    TRANSITIONS,
    Action,
    Actor,
    InvalidTransition,
    touches_canonical,
    transition,
)

S = ArticleStatus

# The specification (04_GOVERNANCE_CONSENT_QDRANT.md, chunk 4.2), written out independently of
# the implementation's table.
EXPECTED: dict[tuple[ArticleStatus, Action, Actor], ArticleStatus] = {
    (S.DRAFT, Action.SUBMIT, Actor.AUTHOR): S.IN_REVIEW,
    (S.IN_REVIEW, Action.REQUEST_CHANGES, Actor.EXPERT): S.DRAFT,
    (S.IN_REVIEW, Action.REJECT, Actor.EXPERT): S.REJECTED,
    (S.IN_REVIEW, Action.APPROVE, Actor.EXPERT): S.CANONICAL,
    (S.CANONICAL, Action.SUPERSEDE, Actor.SYSTEM): S.SUPERSEDED,
    (S.CANONICAL, Action.MARK_DISPUTED, Actor.EXPERT): S.DISPUTED,
    (S.DISPUTED, Action.APPROVE, Actor.EXPERT): S.CANONICAL,
    (S.CANONICAL, Action.DEGRADE, Actor.SYSTEM): S.REVISION_REQUIRED,
    (S.REVISION_REQUIRED, Action.SUBMIT, Actor.AUTHOR): S.IN_REVIEW,
    (S.REVISION_REQUIRED, Action.SUBMIT, Actor.EXPERT): S.IN_REVIEW,
    **{
        (status, Action.WITHDRAW, Actor.SYSTEM): S.REJECTED
        for status in (S.DRAFT, S.IN_REVIEW, S.CANONICAL, S.DISPUTED, S.REVISION_REQUIRED)
    },
}

ALL = list(itertools.product(ArticleStatus, Action, Actor))


@pytest.mark.parametrize(("status", "action", "actor"), ALL)
def test_every_combination_matches_the_specification(
    status: ArticleStatus, action: Action, actor: Actor
) -> None:
    expected = EXPECTED.get((status, action, actor))
    if expected is None:
        with pytest.raises(InvalidTransition):
            transition(status, action, actor)
    else:
        assert transition(status, action, actor) == expected


def test_table_has_no_transition_outside_the_specification() -> None:
    specified = {(status, action) for status, action, _ in EXPECTED}
    assert set(TRANSITIONS) == specified
    for action, actors in ACTORS.items():
        assert actors == {a for (_, act, a) in EXPECTED if act == action}


def test_terminal_statuses_have_no_exit() -> None:
    for status in (S.SUPERSEDED, S.REJECTED):
        for action, actor in itertools.product(Action, Actor):
            with pytest.raises(InvalidTransition):
                transition(status, action, actor)


def test_every_review_decision_maps_to_an_expert_action() -> None:
    assert set(DECISION_ACTION) == set(ReviewDecision)
    assert all(Actor.EXPERT in ACTORS[action] for action in DECISION_ACTION.values())


def test_touches_canonical() -> None:
    assert touches_canonical(S.IN_REVIEW, S.CANONICAL)
    assert touches_canonical(S.CANONICAL, S.DISPUTED)
    assert not touches_canonical(S.DRAFT, S.IN_REVIEW)
    assert not touches_canonical(S.CANONICAL, S.CANONICAL)


def test_vector_ops_round_trip_through_json() -> None:
    author, article = uuid4(), uuid4()
    ops = [
        reindex_articles((article,)),
        deactivate_articles((article,), S.DISPUTED),
        suspend_blocks(author, None),
        resume_blocks(author, (article,), activate=False),
        delete_blocks(author, (article,)),
    ]
    for op in ops:
        assert VectorOp.from_json(op.to_json()) == op
    assert ops[1].payload == {"article_status": "DISPUTED", "is_active": False}
    assert ops[2].payload == {"consent_status": "SUSPENDED", "is_active": False}
    assert "is_active" not in ops[3].payload  # a degraded article stays out of retrieval
    assert resume_blocks(author, None, activate=True).payload["is_active"] is True


def test_vector_ops_refuse_meaningless_scopes() -> None:
    with pytest.raises(ValueError, match="payload"):
        VectorOp(OpKind.SET_PAYLOAD, CanonicalScope(author_id=uuid4()))
    with pytest.raises(ValueError, match="whole articles"):
        VectorOp(OpKind.UPSERT, CanonicalScope(author_id=uuid4()))


@dataclass
class FakeRepo:
    fail: bool = False
    calls: list[tuple[str, CanonicalScope, dict[str, Any] | None]] = field(
        default_factory=list[tuple[str, CanonicalScope, dict[str, Any] | None]]
    )

    async def set_payload(self, scope: CanonicalScope, payload: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("qdrant down")
        self.calls.append(("set_payload", scope, payload))

    async def delete(self, scope: CanonicalScope) -> None:
        if self.fail:
            raise ConnectionError("qdrant down")
        self.calls.append(("delete", scope, None))


async def test_vector_sync_applies_in_order_and_reports_failure() -> None:
    repo = FakeRepo()
    reindexed: list[UUID] = []

    async def reindex(article_id: UUID) -> None:
        reindexed.append(article_id)

    sync = VectorSync(repo, reindex, timeout_seconds=1)  # pyright: ignore[reportArgumentType]
    author, a, b = uuid4(), uuid4(), uuid4()

    assert await sync.apply([delete_blocks(author, None), reindex_articles((a, b))]) is True
    assert [c[0] for c in repo.calls] == ["delete"]
    assert reindexed == [a, b]

    repo.fail = True
    assert await sync.apply([suspend_blocks(author, None), reindex_articles((a,))]) is False
    assert reindexed == [a, b]  # stopped at the first failure
