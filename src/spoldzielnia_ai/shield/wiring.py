"""Building the ``AntiDistillationGuard`` for a request (``POST /rag/ask``).

The only place outside ``shield.guard`` that touches ``RAGService``: it constructs it and
hands it to the guard (the import-linter contract keeps ``api.*`` from importing it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request

from spoldzielnia_ai.rag.deps import get_embedder
from spoldzielnia_ai.rag.service import RAGService
from spoldzielnia_ai.shield.challenge import PowChallenges
from spoldzielnia_ai.shield.deps import get_coverage, get_locker
from spoldzielnia_ai.shield.guard import AntiDistillationGuard
from spoldzielnia_ai.shield.honeypot import HoneypotMonitor
from spoldzielnia_ai.shield.output import OutputShield
from spoldzielnia_ai.shield.risk import RiskEngine, RiskStore
from spoldzielnia_ai.shield.semantic import SemanticAnomalyDetector
from spoldzielnia_ai.shield.watermark import Watermarker
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings


def get_guard(request: Request) -> AntiDistillationGuard:
    state = request.app.state
    settings: Settings = state.settings
    shield = settings.shield
    redis, factory = state.redis, state.session_factory
    locker = get_locker(request)
    store = RiskStore(redis, shield, factory)
    rag = RAGService(
        factory, CanonicalVectorRepo(state.qdrant), get_embedder(request), state.llm, settings.rag
    )
    return AntiDistillationGuard(
        rag=rag,
        coverage=get_coverage(request),
        semantic=SemanticAnomalyDetector(redis, shield),
        honeypots=HoneypotMonitor(redis, factory, store, locker, state.notifier, shield),
        output=OutputShield(factory, store, shield, Watermarker.from_settings(shield)),
        risk=RiskEngine(store, shield),
        incidents=store,
        challenges=PowChallenges(
            redis,
            shield.pow_difficulty_bits,
            shield.pow_ttl_seconds,
            shield.pow_escalation_bits,
            shield.pow_max_bits,
        ),
        locker=locker,
        factory=factory,
        settings=shield,
        rag_settings=settings.rag,
    )


GuardDep = Annotated[AntiDistillationGuard, Depends(get_guard)]
