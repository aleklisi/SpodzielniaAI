"""Chunks 4.2/4.4: the review state machine (exhaustively), the vector operations it produces,
and how they are applied and scheduled."""

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
    backoff,
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
from spoldzielnia_ai.workers.celery_adapter import beat_schedule
from spoldzielnia_ai.workers.jobs import JOB_MODEL, MaintenanceJob, TaskName

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


async def test_vector_sync_dispatches_each_op_and_raises_on_failure() -> None:
    repo = FakeRepo()
    reindexed: list[UUID] = []

    async def reindex(article_id: UUID) -> None:
        reindexed.append(article_id)

    sync = VectorSync(None, repo, reindex, timeout_seconds=1)  # pyright: ignore[reportArgumentType]
    author, a, b = uuid4(), uuid4(), uuid4()

    await sync.apply_one(delete_blocks(author, None))
    await sync.apply_one(reindex_articles((a, b)))
    await sync.apply_one(suspend_blocks(author, (a,)))
    assert [c[0] for c in repo.calls] == ["delete", "set_payload"]
    assert reindexed == [a, b]

    repo.fail = True
    with pytest.raises(ConnectionError):
        await sync.apply_one(suspend_blocks(author, None))  # the outbox keeps it for a retry


def test_outbox_backoff_is_exponential_and_capped() -> None:
    assert [backoff(n).total_seconds() for n in (1, 2, 3, 4, 5, 6, 10)] == [
        30,
        60,
        120,
        240,
        480,
        600,
        600,
    ]


def test_beat_schedules_reconciliation_and_nightly_audit() -> None:
    schedule = beat_schedule()

    reconcile = schedule["reconcile-vector-outbox"]
    assert reconcile["task"] == TaskName.RECONCILE_OUTBOX.value
    assert reconcile["schedule"] == 30.0
    assert reconcile["options"]["expires"] < reconcile["schedule"]
    assert schedule["vector-consistency-audit"]["task"] == TaskName.CONSISTENCY_AUDIT.value
    assert JOB_MODEL[TaskName.RECONCILE_OUTBOX] is MaintenanceJob
    assert MaintenanceJob.model_validate({}).to_message() == {}
