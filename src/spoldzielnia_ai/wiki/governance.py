"""Article review state machine (chunk 4.2): an explicit, pure transition table.

    DRAFT              --SUBMIT (author)---------> IN_REVIEW
    IN_REVIEW          --REQUEST_CHANGES (expert)-> DRAFT
    IN_REVIEW          --REJECT (expert)----------> REJECTED
    IN_REVIEW          --APPROVE (expert)---------> CANONICAL
    CANONICAL          --SUPERSEDE (system)-------> SUPERSEDED   newer article approved
    CANONICAL          --MARK_DISPUTED (expert)---> DISPUTED
    DISPUTED           --APPROVE (expert)---------> CANONICAL
    CANONICAL          --DEGRADE (system)---------> REVISION_REQUIRED   a co-author withdrew
    REVISION_REQUIRED  --SUBMIT (author/expert)---> IN_REVIEW

``WITHDRAW`` (every author of the article revoked consent, 4.3) archives any live article as
``REJECTED``. Who may perform an action is part of the table too: authors submit, experts
decide, and only the system supersedes, degrades and withdraws.
"""

from __future__ import annotations

from enum import StrEnum

from spoldzielnia_ai.db.enums import ArticleStatus, ReviewDecision

S = ArticleStatus


class Action(StrEnum):
    SUBMIT = "SUBMIT"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    REJECT = "REJECT"
    APPROVE = "APPROVE"
    MARK_DISPUTED = "MARK_DISPUTED"
    SUPERSEDE = "SUPERSEDE"
    DEGRADE = "DEGRADE"
    WITHDRAW = "WITHDRAW"


class Actor(StrEnum):
    AUTHOR = "AUTHOR"
    EXPERT = "EXPERT"
    SYSTEM = "SYSTEM"


TRANSITIONS: dict[tuple[ArticleStatus, Action], ArticleStatus] = {
    (S.DRAFT, Action.SUBMIT): S.IN_REVIEW,
    (S.IN_REVIEW, Action.REQUEST_CHANGES): S.DRAFT,
    (S.IN_REVIEW, Action.REJECT): S.REJECTED,
    (S.IN_REVIEW, Action.APPROVE): S.CANONICAL,
    (S.CANONICAL, Action.SUPERSEDE): S.SUPERSEDED,
    (S.CANONICAL, Action.MARK_DISPUTED): S.DISPUTED,
    (S.DISPUTED, Action.APPROVE): S.CANONICAL,
    (S.CANONICAL, Action.DEGRADE): S.REVISION_REQUIRED,
    (S.REVISION_REQUIRED, Action.SUBMIT): S.IN_REVIEW,
    **{
        (status, Action.WITHDRAW): S.REJECTED
        for status in (S.DRAFT, S.IN_REVIEW, S.CANONICAL, S.DISPUTED, S.REVISION_REQUIRED)
    },
}

ACTORS: dict[Action, frozenset[Actor]] = {
    Action.SUBMIT: frozenset({Actor.AUTHOR, Actor.EXPERT}),
    Action.REQUEST_CHANGES: frozenset({Actor.EXPERT}),
    Action.REJECT: frozenset({Actor.EXPERT}),
    Action.APPROVE: frozenset({Actor.EXPERT}),
    Action.MARK_DISPUTED: frozenset({Actor.EXPERT}),
    Action.SUPERSEDE: frozenset({Actor.SYSTEM}),
    Action.DEGRADE: frozenset({Actor.SYSTEM}),
    Action.WITHDRAW: frozenset({Actor.SYSTEM}),
}

DECISION_ACTION: dict[ReviewDecision, Action] = {
    ReviewDecision.APPROVE: Action.APPROVE,
    ReviewDecision.REQUEST_CHANGES: Action.REQUEST_CHANGES,
    ReviewDecision.REJECT: Action.REJECT,
    ReviewDecision.MARK_DISPUTED: Action.MARK_DISPUTED,
}

# What an expert may resubmit: an article the system sent back. A DRAFT is its author's.
EXPERT_SUBMITTABLE = frozenset({S.REVISION_REQUIRED})

# Statuses an expert acts on (the review queue).
REVIEWABLE = frozenset({S.IN_REVIEW, S.DISPUTED, S.REVISION_REQUIRED})


class InvalidTransition(Exception):
    def __init__(self, status: ArticleStatus, action: Action, actor: Actor | None = None) -> None:
        by = f" by {actor.value}" if actor else ""
        super().__init__(f"{action.value}{by} is not allowed in {status.value}")
        self.status = status
        self.action = action
        self.actor = actor


def transition(status: ArticleStatus, action: Action, actor: Actor) -> ArticleStatus:
    """The status after ``actor`` performs ``action`` on an article in ``status``."""
    if actor not in ACTORS[action]:
        raise InvalidTransition(status, action, actor)
    target = TRANSITIONS.get((status, action))
    if target is None:
        raise InvalidTransition(status, action)
    if actor == Actor.EXPERT and action == Action.SUBMIT and status not in EXPERT_SUBMITTABLE:
        raise InvalidTransition(status, action, actor)
    return target


def touches_canonical(source: ArticleStatus, target: ArticleStatus) -> bool:
    """The transition enters or leaves CANONICAL: retrievable knowledge changes (EXPERT + MFA,
    unless the system does it)."""
    return S.CANONICAL in (source, target) and source != target
