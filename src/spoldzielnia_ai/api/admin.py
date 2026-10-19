"""``/admin``: shield administration (ADMIN with MFA on every route).

- Honeypots (5.4): inject canary blocks, list them (signatures, never their text) and trace
  leaked text back to the canaries it contains.
- Security console (6.6): audit log (filters, keyset pagination) and its hash-chain status,
  coverage and risk per account, alerts, unlocking an account. Every read of the console is
  itself audited (``SECURITY_DASHBOARD_ACCESSED``).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.verify import verify_chain
from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import ConsentStatus, Role
from spoldzielnia_ai.llm.gateway import LLMError
from spoldzielnia_ai.rag.deps import get_embedder
from spoldzielnia_ai.shield.dashboard import (
    NotLocked,
    alerts,
    audit_page,
    coverage_overview,
    record_access,
    unlock,
)
from spoldzielnia_ai.shield.deps import get_corpus, get_coverage
from spoldzielnia_ai.shield.honeypot import (
    MAX_PER_INJECTION,
    HoneypotInjector,
    HoneypotKeyMissing,
    list_canaries,
    trace,
)
from spoldzielnia_ai.shield.risk import RiskEngine, RiskStore
from spoldzielnia_ai.vector.deps import get_vector_sync

if TYPE_CHECKING:
    from spoldzielnia_ai.db.models import SecurityAuditLog

router = elevated_router(Role.ADMIN, prefix="/admin", tags=["admin"])

SCAN_MAX_CHARS = 1_000_000


class InjectIn(BaseModel):
    count: int = Field(default=5, ge=1, le=MAX_PER_INJECTION)


class CanaryOut(BaseModel):
    block_id: UUID
    signature: str


class InjectOut(BaseModel):
    article_id: UUID
    honeypots: list[CanaryOut]
    index_synced: bool


class StoredCanaryOut(BaseModel):
    block_id: UUID
    signatures: list[str]
    consent_status: ConsentStatus
    created_at: datetime


class ScanIn(BaseModel):
    text: str = Field(min_length=1, max_length=SCAN_MAX_CHARS)


class ScanOut(BaseModel):
    signatures: list[str]
    honeypot_ids: list[UUID]


def _no_key() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "honeypot_key_missing"}
    )


@router.post("/honeypots", response_model=InjectOut, status_code=status.HTTP_201_CREATED)
async def inject_honeypots(
    payload: InjectIn, request: Request, principal: CurrentUser
) -> InjectOut:
    state = request.app.state
    injector = HoneypotInjector(
        state.session_factory,
        get_embedder(request),
        get_vector_sync(request),
        state.settings.shield,
        state.redis,
    )
    try:
        result = await injector.inject(payload.count, principal.id)
    except HoneypotKeyMissing:
        raise _no_key() from None
    except LLMError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "model_unavailable"}
        ) from None
    return InjectOut(
        article_id=result.article_id,
        honeypots=[CanaryOut(block_id=c.block_id, signature=c.signature) for c in result.canaries],
        index_synced=result.index_synced,
    )


@router.get("/honeypots", response_model=list[StoredCanaryOut])
async def get_honeypots(request: Request) -> list[StoredCanaryOut]:
    state = request.app.state
    try:
        canaries = await list_canaries(state.session_factory, state.settings.shield)
    except HoneypotKeyMissing:
        raise _no_key() from None
    return [
        StoredCanaryOut(
            block_id=c.block_id,
            signatures=c.signatures,
            consent_status=c.consent_status,
            created_at=c.created_at,
        )
        for c in canaries
    ]


@router.post("/honeypots/scan", response_model=ScanOut)
async def scan_text(payload: ScanIn, request: Request) -> ScanOut:
    """Forensics: which canaries does this (leaked) text contain?"""
    state = request.app.state
    try:
        signatures, blocks = await trace(state.session_factory, state.settings.shield, payload.text)
    except HoneypotKeyMissing:
        raise _no_key() from None
    return ScanOut(signatures=signatures, honeypot_ids=blocks)


# --- Security console (6.6) --------------------------------------------------------------------


class AuditEntryOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_id: UUID | None
    event_type: str
    target_type: str | None
    target_id: str | None
    payload: dict[str, Any]


class AuditPageOut(BaseModel):
    items: list[AuditEntryOut]
    next_cursor: int | None


class ChainBreakOut(BaseModel):
    id: int
    reason: str


class ChainOut(BaseModel):
    ok: bool
    rows: int
    head_id: int | None
    head_hash: str
    breaks: list[ChainBreakOut]


class UserCoverageOut(BaseModel):
    user_id: UUID
    display_name: str | None
    email: str | None
    roles: list[str]
    is_locked: bool
    locked_reason: str | None
    locked_at: datetime | None
    ratios: dict[str, float]
    blocks: dict[str, int]
    fraction_of_limit: float
    pressure: float
    incidents: float
    risk: float
    level: str


class CoverageOut(BaseModel):
    corpus_blocks: int
    limits: dict[str, float]
    min_blocks_before_lock: int
    users: list[UserCoverageOut]


class UnlockIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    reset_risk: bool = True


class UnlockOut(BaseModel):
    user_id: UUID
    is_locked: bool


def _entry(row: SecurityAuditLog) -> AuditEntryOut:
    return AuditEntryOut(
        id=row.id,
        occurred_at=row.occurred_at,
        actor_id=row.actor_id,
        event_type=row.event_type,
        target_type=row.target_type,
        target_id=row.target_id,
        payload=dict(row.payload),
    )


@router.get("/audit-log", response_model=AuditPageOut)
async def audit_log(
    request: Request,
    principal: CurrentUser,
    event_type: Annotated[list[str] | None, Query()] = None,
    actor: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPageOut:
    factory = request.app.state.session_factory
    known = {e.value for e in EventType}
    types = [t for t in (event_type or []) if t in known]
    page = await audit_page(
        factory,
        event_types=types,
        actor_id=actor,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )
    await record_access(
        factory,
        principal.id,
        "audit_log",
        {"event_type": list(types) or None, "actor": str(actor) if actor else None},
    )
    return AuditPageOut(items=[_entry(r) for r in page.items], next_cursor=page.next_cursor)


@router.get("/audit-log/verify", response_model=ChainOut)
async def audit_log_verify(request: Request, principal: CurrentUser) -> ChainOut:
    """Re-compute the hash chain (1.4). Anchor ``head_id``/``head_hash`` outside the system."""
    report = await verify_chain(request.app.state.engine)
    await record_access(request.app.state.session_factory, principal.id, "audit_verify", {})
    return ChainOut(
        ok=report.ok,
        rows=report.rows,
        head_id=report.head_id,
        head_hash=report.head_hash.hex(),
        breaks=[ChainBreakOut(id=b.id, reason=b.reason) for b in report.breaks],
    )


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    request: Request,
    principal: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> CoverageOut:
    state = request.app.state
    shield = state.settings.shield
    store = RiskStore(state.redis, shield, state.session_factory)
    tracker = get_coverage(request)
    users = await coverage_overview(
        state.session_factory, tracker, store, RiskEngine(store, shield), shield, limit
    )
    corpus = await get_corpus(request).size()
    await record_access(state.session_factory, principal.id, "coverage", {"limit": limit})
    return CoverageOut(
        corpus_blocks=corpus.blocks,
        limits={
            "24h": shield.coverage_24h_max,
            "7d": shield.coverage_7d_max,
            "30d": shield.coverage_30d_max,
        },
        min_blocks_before_lock=shield.min_blocks_before_lock,
        users=[
            UserCoverageOut(
                user_id=u.user.id,
                display_name=u.user.display_name,
                email=u.user.email,
                roles=[r.value for r in u.user.roles],
                is_locked=u.user.is_locked,
                locked_reason=u.user.locked_reason,
                locked_at=u.user.locked_at,
                ratios=u.ratios,
                blocks=u.blocks,
                fraction_of_limit=u.fraction_of_limit,
                pressure=u.pressure,
                incidents=u.incidents,
                risk=u.risk,
                level=u.level.value,
            )
            for u in users
        ],
    )


@router.get("/alerts", response_model=list[AuditEntryOut])
async def get_alerts(
    request: Request,
    principal: CurrentUser,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEntryOut]:
    factory = request.app.state.session_factory
    rows = await alerts(factory, since, limit)
    await record_access(factory, principal.id, "alerts", {})
    return [_entry(r) for r in rows]


@router.post("/users/{user_id}/unlock", response_model=UnlockOut)
async def unlock_user(
    user_id: UUID, payload: UnlockIn, request: Request, principal: CurrentUser
) -> UnlockOut:
    state = request.app.state
    store = RiskStore(state.redis, state.settings.shield, state.session_factory)
    try:
        user = await unlock(
            state.session_factory,
            state.redis,
            store,
            principal.id,
            user_id,
            payload.reason.strip(),
            payload.reset_risk,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from None
    except NotLocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"error": "not_locked"}
        ) from None
    return UnlockOut(user_id=user.id, is_locked=user.is_locked)
