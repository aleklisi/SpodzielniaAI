"""``/admin``: shield administration (ADMIN with MFA on every route).

Honeypots (5.4): inject canary blocks, list them (signatures, never their text) and trace
leaked text back to the canaries it contains.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field

from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import ConsentStatus, Role
from spoldzielnia_ai.llm.gateway import LLMError
from spoldzielnia_ai.rag.deps import get_embedder
from spoldzielnia_ai.shield.honeypot import (
    MAX_PER_INJECTION,
    HoneypotInjector,
    HoneypotKeyMissing,
    list_canaries,
    trace,
)
from spoldzielnia_ai.vector.deps import get_vector_sync

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
