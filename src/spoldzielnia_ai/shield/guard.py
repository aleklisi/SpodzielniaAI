"""``AntiDistillationGuard``: the only way from the API to cooperative RAG (5.6).

Before retrieval
  1. Embed the question (locally), check it for extraction intent (risk +, strict prompt).
  2. Semantic traversal score of the session with this query (5.3).
  3. Coverage so far (5.2): an account already over a limit stops here.
  4. RiskEngine level → NORMAL / ELEVATED (top_k 4, shorter, slower) / HIGH (top_k 2 and a
     step-up: a fresh MFA login or one solved proof-of-work per request; ignoring the
     challenges raises the risk further) / CRITICAL (lock).
During retrieval (``RetrievalGate``)
  5. Honeypots (5.4): audited, risk +, removed; repeated hits lock the account.
  6. Coverage including this request's blocks: the request that would cross a limit is the
     one refused, and nothing of it is recorded.
After synthesis
  7. OutputVerifier (5.5): verbatim drafts become a refusal with citations; others are
     watermarked for the account. The client gets a ``RagAnswer`` only.

Degradation is silent: the member never learns which signal fired. Every decision at
ELEVATED or above is audited as ``SHIELD_DECISION`` with its features (numbers, never text).
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.rag.synthesis import Style
from spoldzielnia_ai.shield.coverage import CoverageExceeded, CoverageLevel
from spoldzielnia_ai.shield.deps import coverage_refusal, security_lock
from spoldzielnia_ai.shield.output_verifier import extraction_intent
from spoldzielnia_ai.shield.risk import RiskLevel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.core.settings import RagSettings, ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.rag.schemas import RagAnswer
    from spoldzielnia_ai.rag.service import RAGService, Retrieval
    from spoldzielnia_ai.shield.challenge import PowChallenges
    from spoldzielnia_ai.shield.coverage import CoverageTracker
    from spoldzielnia_ai.shield.honeypot import HoneypotHit, HoneypotMonitor
    from spoldzielnia_ai.shield.locks import Locker
    from spoldzielnia_ai.shield.output import OutputShield
    from spoldzielnia_ai.shield.risk import Assessment, RiskEngine, RiskStore
    from spoldzielnia_ai.shield.semantic import SemanticAnomalyDetector
    from spoldzielnia_ai.vector.revalidate import LiveBlock

log = get_logger(__name__)


class ShieldBlocked(HTTPException):
    """A request the shield refuses (FastAPI renders it like any ``HTTPException``)."""


@dataclass(frozen=True, slots=True)
class Asker:
    user_id: UUID
    mfa_satisfied: bool
    auth_time: int | None
    step_up_url: str

    def fresh_mfa(self, max_age_seconds: int) -> bool:
        return (
            self.mfa_satisfied
            and self.auth_time is not None
            and time.time() - self.auth_time <= max_age_seconds
        )


@dataclass(frozen=True, slots=True)
class Plan:
    top_k: int
    style: Style
    delay: bool


def _jitter(low_ms: int, high_ms: int) -> float:
    return (low_ms + secrets.randbelow(high_ms - low_ms + 1)) / 1000


class _Gate:
    """``RetrievalGate`` for one request: honeypots, then the coverage budget."""

    def __init__(self, guard: AntiDistillationGuard, user_id: UUID, question: str) -> None:
        self._guard = guard
        self._user_id = user_id
        self._question = question
        self.honeypot: HoneypotHit | None = None

    async def admit(self, retrieval: Retrieval) -> list[LiveBlock]:
        g = self._guard
        self.honeypot = await g.honeypots.inspect(self._user_id, self._question, retrieval.blocks)
        if self.honeypot is not None and self.honeypot.locked:
            raise ShieldBlocked(**_kwargs(security_lock()))
        allowed = [b for b in retrieval.blocks if not b.is_honeypot]
        articles = list(dict.fromkeys(b.article_id for b in allowed))
        try:
            await g.coverage.admit(self._user_id, [b.block_id for b in allowed], articles)
        except CoverageExceeded as exc:
            raise ShieldBlocked(**_kwargs(coverage_refusal(exc))) from None
        return allowed


def _kwargs(exc: HTTPException) -> dict[str, Any]:
    return {"status_code": exc.status_code, "detail": exc.detail, "headers": exc.headers}


class AntiDistillationGuard:
    def __init__(
        self,
        *,
        rag: RAGService,
        coverage: CoverageTracker,
        semantic: SemanticAnomalyDetector,
        honeypots: HoneypotMonitor,
        output: OutputShield,
        risk: RiskEngine,
        incidents: RiskStore,
        challenges: PowChallenges,
        locker: Locker,
        factory: SessionFactory,
        settings: ShieldSettings,
        rag_settings: RagSettings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rag = rag
        self.coverage = coverage
        self.semantic = semantic
        self.honeypots = honeypots
        self.output = output
        self.risk = risk
        self.incidents = incidents
        self.challenges = challenges
        self.locker = locker
        self._factory = factory
        self._settings = settings
        self._rag_settings = rag_settings
        self._sleep = sleep

    def _plan(self, level: RiskLevel, strict: bool) -> Plan:
        s, r = self._settings, self._rag_settings
        match level:
            case RiskLevel.NORMAL:
                return Plan(r.top_k, Style(strict, r.answer_max_words), delay=False)
            case RiskLevel.ELEVATED:
                return Plan(s.elevated_top_k, Style(strict, s.elevated_max_words), delay=True)
            case _:
                return Plan(s.high_top_k, Style(strict, s.elevated_max_words), delay=True)

    async def ask(self, asker: Asker, question: str, pow_solution: str | None = None) -> RagAnswer:
        s = self._settings
        user = asker.user_id
        request_id = uuid7()
        vector = await self.rag.embed(question)
        intent = extraction_intent(question)
        if intent:
            await self.incidents.add(user, s.extraction_intent_penalty)
        semantic = await self.semantic.observe(user, question, vector)
        coverage = await self.coverage.check(user)
        if coverage.level == CoverageLevel.LOCK:
            try:
                await self.coverage.refuse(user, coverage)
            except CoverageExceeded as exc:
                raise ShieldBlocked(**_kwargs(coverage_refusal(exc))) from None
        assessment = await self.risk.assess(user, coverage, semantic)
        features: dict[str, JsonValue] = {
            **assessment.to_json(),
            "semantic": semantic.features.to_json(),
            "coverage": {w.value: round(r, 4) for w, r in coverage.ratio_by_window.items()},
            "extraction_intent": list(intent),
        }

        if assessment.level == RiskLevel.CRITICAL:
            await self.locker.lock(user, "risk_critical", EventType.SHIELD_DECISION, features)
            log.warning("shield.critical", user_id=str(user), score=assessment.score)
            raise ShieldBlocked(**_kwargs(security_lock()))
        if assessment.level == RiskLevel.HIGH:
            await self._step_up(asker, pow_solution, request_id, features)

        plan = self._plan(assessment.level, strict=bool(intent))
        gate = _Gate(self, user, question)
        outcome = await self.rag.ask(
            question, top_k=plan.top_k, style=plan.style, gate=gate, vector=vector
        )
        if plan.delay:
            await self._sleep(_jitter(*s.elevated_delay_ms))
        blocked = False
        if outcome.draft is None:
            answer = self.rag.no_position(request_id)
        else:
            checked = await self.output.check(
                user, request_id, outcome.draft.answer_markdown, outcome.sources
            )
            blocked = checked.blocked
            answer = await self.rag.answer(outcome, checked.text, request_id)
        if assessment.level != RiskLevel.NORMAL:
            await self._decision(
                user,
                request_id,
                assessment,
                {
                    **features,
                    "top_k": plan.top_k,
                    "blocks": len(outcome.admitted),
                    "verbatim_blocked": blocked,
                    "honeypot": gate.honeypot is not None,
                },
            )
        return answer

    async def _step_up(
        self,
        asker: Asker,
        pow_solution: str | None,
        request_id: UUID,
        features: dict[str, JsonValue],
    ) -> None:
        s = self._settings
        if asker.fresh_mfa(s.step_up_max_auth_age_seconds):
            return
        if await self.challenges.redeem(asker.user_id, pow_solution):
            return
        if await self.challenges.open(asker.user_id) >= s.step_up_unresolved_max:
            # Challenge after challenge left unsolved: push the account towards CRITICAL.
            score = await self.incidents.add(asker.user_id, s.unresolved_challenge_penalty)
            features = {**features, "unresolved_challenges": True, "incidents_now": score}
        puzzle = await self.challenges.issue(asker.user_id)
        challenge = "mfa_step_up" if asker.mfa_satisfied else "pow"
        await self._audit(asker.user_id, request_id, {**features, "challenge": challenge})
        detail: dict[str, Any] = {"challenge": challenge, "pow": puzzle.to_json()}
        if challenge == "mfa_step_up":
            detail["step_up"] = asker.step_up_url
        raise ShieldBlocked(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    async def _decision(
        self,
        user_id: UUID,
        request_id: UUID,
        assessment: Assessment,
        features: dict[str, JsonValue],
    ) -> None:
        log.info("shield.decision", user_id=str(user_id), level=assessment.level.value)
        await self._audit(user_id, request_id, features)

    async def _audit(self, user_id: UUID, request_id: UUID, features: dict[str, JsonValue]) -> None:
        async with self._factory() as session, session.begin():
            await AuditService(session).record(
                EventType.SHIELD_DECISION,
                user_id,
                AuditTarget("rag_request", str(request_id)),
                features,
            )
