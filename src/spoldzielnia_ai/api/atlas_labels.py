"""Atlas domains and label vocabulary (chunk 9.1).

- ``/atlas/domains``, ``/atlas/labels``, ``/atlas/labels/proposals``: every member. A member
  sees ACTIVE labels and their own proposals, and may propose (daily limit).
- ``/atlas/curation/labels…``: EXPERT or ADMIN + MFA. Create, edit, approve, reject, merge,
  deprecate; the list shows every status.
- ``/admin/atlas/domains…``: ADMIN + MFA. Create, rename, archive.

``LabelError`` codes become ``{"detail": {"error": code}}``.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from spoldzielnia_ai.atlas.labels import LabelError, LabelService
from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import Role, VisualDomainStatus, VisualLabelStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import VisualDomain, VisualLabel
    from spoldzielnia_ai.db.session import SessionFactory

router = APIRouter(prefix="/atlas", tags=["atlas"])
curation_router = elevated_router(Role.EXPERT, Role.ADMIN, prefix="/atlas/curation", tags=["atlas"])
admin_router = elevated_router(Role.ADMIN, prefix="/admin/atlas", tags=["atlas"])

Name = Annotated[str, Field(min_length=1, max_length=100)]
Description = Annotated[str | None, Field(default=None, max_length=1000)]


class DomainOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    status: VisualDomainStatus


class LabelOut(BaseModel):
    id: UUID
    domain_id: UUID
    slug: str
    name: str
    description: str | None
    synonyms: list[str]
    status: VisualLabelStatus
    merged_into_id: UUID | None
    is_own_proposal: bool


class CuratedLabelOut(LabelOut):
    proposed_by_name: str | None
    created_at: dt.datetime


class DomainIn(BaseModel):
    name: Name
    description: Description


class DomainPatch(BaseModel):
    name: Name | None = None
    description: Description
    status: VisualDomainStatus | None = None


class ProposalIn(BaseModel):
    domain_id: UUID
    name: Name
    description: Description


class LabelIn(ProposalIn):
    synonyms: list[Annotated[str, Field(max_length=100)]] = Field(default=[], max_length=20)


class LabelPatch(BaseModel):
    name: Name | None = None
    description: Description
    synonyms: list[Annotated[str, Field(max_length=100)]] | None = Field(
        default=None, max_length=20
    )


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class MergeIn(BaseModel):
    into_id: UUID


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@asynccontextmanager
async def _transaction(request: Request) -> AsyncGenerator[LabelService]:
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session, session.begin():
        yield LabelService(session)


def _fail(error: LabelError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


def _domain(domain: VisualDomain) -> DomainOut:
    return DomainOut.model_validate(domain, from_attributes=True)


def _label(label: VisualLabel, viewer: UUID) -> LabelOut:
    return LabelOut(
        id=label.id,
        domain_id=label.domain_id,
        slug=label.slug,
        name=label.name,
        description=label.description,
        synonyms=list(label.synonyms),
        status=label.status,
        merged_into_id=label.merged_into_id,
        is_own_proposal=label.proposed_by == viewer and label.status == VisualLabelStatus.PROPOSED,
    )


async def _within_daily_limit(redis: Redis, user_id: UUID, limit: int) -> bool:
    key = f"atlas:label-proposals:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 24 * 3600)
    return count <= limit


# --- Members -----------------------------------------------------------------------------------


@router.get("/domains", response_model=list[DomainOut])
async def list_domains(request: Request, _user: CurrentUser) -> list[DomainOut]:
    """All domains; archived ones only label existing photos (the UI offers ACTIVE ones)."""
    async with _transaction(request) as labels:
        return [_domain(d) for d in await labels.domains()]


@router.get("/labels", response_model=list[LabelOut])
async def list_labels(
    request: Request,
    user: CurrentUser,
    domain_id: UUID | None = None,
    q: Annotated[str, Query(max_length=100)] = "",
) -> list[LabelOut]:
    """ACTIVE labels and the caller's own proposals, searched by name and synonyms (diacritics
    don't matter)."""
    async with _transaction(request) as labels:
        found = await labels.search(user.id, domain_id=domain_id, q=q)
        return [_label(label, user.id) for label in found]


@router.post("/labels/proposals", status_code=status.HTTP_201_CREATED, response_model=LabelOut)
async def propose_label(payload: ProposalIn, request: Request, user: CurrentUser) -> LabelOut:
    settings = _settings(request)
    limit = settings.atlas.label_proposals_per_day
    if not await _within_daily_limit(request.app.state.redis, user.id, limit):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"})
    try:
        async with _transaction(request) as labels:
            label = await labels.propose(
                user.id, payload.domain_id, payload.name, payload.description
            )
            return _label(label, user.id)
    except LabelError as exc:
        raise _fail(exc) from None


# --- Experts -----------------------------------------------------------------------------------


async def _with_proposers(
    labels: LabelService, rows: Sequence[VisualLabel], viewer: UUID
) -> list[CuratedLabelOut]:
    names = await labels.user_names({r.proposed_by for r in rows if r.proposed_by is not None})
    return [
        CuratedLabelOut(
            **_label(row, viewer).model_dump(),
            proposed_by_name=names.get(row.proposed_by) if row.proposed_by else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


@curation_router.get("/labels", response_model=list[CuratedLabelOut])
async def curate_list(
    request: Request,
    user: CurrentUser,
    domain_id: UUID | None = None,
    q: Annotated[str, Query(max_length=100)] = "",
    label_status: Annotated[list[VisualLabelStatus] | None, Query(alias="status")] = None,
) -> list[CuratedLabelOut]:
    async with _transaction(request) as labels:
        rows = await labels.search(
            user.id,
            domain_id=domain_id,
            q=q,
            statuses=label_status,
            curator=True,
            limit=200,
        )
        return await _with_proposers(labels, rows, user.id)


@curation_router.post("/labels", status_code=status.HTTP_201_CREATED, response_model=LabelOut)
async def create_label(payload: LabelIn, request: Request, user: CurrentUser) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            label = await labels.create(
                user.id, payload.domain_id, payload.name, payload.description, payload.synonyms
            )
            return _label(label, user.id)
    except LabelError as exc:
        raise _fail(exc) from None


@curation_router.patch("/labels/{label_id}", response_model=LabelOut)
async def edit_label(
    label_id: UUID, payload: LabelPatch, request: Request, user: CurrentUser
) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            label = await labels.edit(
                user.id,
                label_id,
                name=payload.name,
                description=payload.description,
                synonyms=payload.synonyms,
            )
            return _label(label, user.id)
    except LabelError as exc:
        raise _fail(exc) from None


@curation_router.post("/labels/{label_id}/approve", response_model=LabelOut)
async def approve_label(label_id: UUID, request: Request, user: CurrentUser) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            return _label(await labels.approve(user.id, label_id), user.id)
    except LabelError as exc:
        raise _fail(exc) from None


@curation_router.post("/labels/{label_id}/reject", response_model=LabelOut)
async def reject_label(
    label_id: UUID, payload: RejectIn, request: Request, user: CurrentUser
) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            return _label(await labels.reject(user.id, label_id, payload.reason), user.id)
    except LabelError as exc:
        raise _fail(exc) from None


@curation_router.post("/labels/{label_id}/merge", response_model=LabelOut)
async def merge_label(
    label_id: UUID, payload: MergeIn, request: Request, user: CurrentUser
) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            return _label(await labels.merge(user.id, label_id, payload.into_id), user.id)
    except LabelError as exc:
        raise _fail(exc) from None


@curation_router.post("/labels/{label_id}/deprecate", response_model=LabelOut)
async def deprecate_label(label_id: UUID, request: Request, user: CurrentUser) -> LabelOut:
    try:
        async with _transaction(request) as labels:
            return _label(await labels.deprecate(user.id, label_id), user.id)
    except LabelError as exc:
        raise _fail(exc) from None


# --- Admins ------------------------------------------------------------------------------------


@admin_router.post("/domains", status_code=status.HTTP_201_CREATED, response_model=DomainOut)
async def create_domain(payload: DomainIn, request: Request, user: CurrentUser) -> DomainOut:
    try:
        async with _transaction(request) as labels:
            return _domain(await labels.create_domain(user.id, payload.name, payload.description))
    except LabelError as exc:
        raise _fail(exc) from None


@admin_router.patch("/domains/{domain_id}", response_model=DomainOut)
async def update_domain(
    domain_id: UUID, payload: DomainPatch, request: Request, user: CurrentUser
) -> DomainOut:
    try:
        async with _transaction(request) as labels:
            domain = await labels.update_domain(
                user.id,
                domain_id,
                name=payload.name,
                description=payload.description,
                status=payload.status,
            )
            return _domain(domain)
    except LabelError as exc:
        raise _fail(exc) from None


__all__ = ["admin_router", "curation_router", "router"]
