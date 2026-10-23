"""„Gdzie przetwarzane są Twoje dane”: where private data is processed (chunk 10.8, P4).

For every signed-in member: the model providers cleared for private data (within the operator's
list), with the data region, retention, contract reference, since when, whether they are on the
cooperative's server or in the cloud, and which private purposes the active configuration sends
them; plus the purposes served by built-in engines on the server itself. Never keys, models'
endpoints beyond the host name, or anything about other members.

A cloud provider cleared after the member last acknowledged the list (``processors_seen_at``)
turns on the one-time banner; ``POST /privacy/processors/seen`` acknowledges it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select, update

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import LlmProviderKind, LlmRevisionStatus, LlmRoutePurpose
from spoldzielnia_ai.db.models import LlmConfigRevision, LlmProvider, User
from spoldzielnia_ai.llm.capabilities import builtin_purposes
from spoldzielnia_ai.llm.config_service import PRIVATE_ROUTE_PURPOSES, provider_cleared
from spoldzielnia_ai.llm.endpoints import EndpointNotAllowed, classify
from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot

router = APIRouter(tags=["privacy"])


class ProcessorOut(BaseModel):
    name: str
    host: str
    on_server: bool  # the cooperative's own (local) endpoint, as opposed to a cloud API
    region: str | None
    retention: str | None
    contract_ref: str | None
    cleared_since: datetime | None
    purposes: list[LlmRoutePurpose]


class ProcessorsOut(BaseModel):
    processors: list[ProcessorOut]
    # Private purposes served by built-in engines on the server (no provider involved).
    built_in: list[LlmRoutePurpose]
    banner: bool


def _host(url: str) -> str:
    host: str | None = urlsplit(url).hostname
    return host or ""


@router.get("/privacy/processors", response_model=ProcessorsOut)
async def get_processors(request: Request, principal: CurrentUser) -> ProcessorsOut:
    settings = request.app.state.settings.llm
    async with request.app.state.session_factory() as session:
        providers = [
            p
            for p in await session.scalars(
                select(LlmProvider)
                .where(LlmProvider.disabled_at.is_(None))
                .order_by(LlmProvider.cleared_at)
            )
            if provider_cleared(p, settings)
        ]
        snapshot = await session.scalar(
            select(LlmConfigRevision.snapshot).where(
                LlmConfigRevision.status == LlmRevisionStatus.ACTIVE
            )
        )
        seen = await session.scalar(select(User.processors_seen_at).where(User.id == principal.id))
    routed: dict[object, set[LlmRoutePurpose]] = {}
    if snapshot is not None and settings.config_source == "db":
        config = ConfigSnapshot.model_validate(snapshot)
        for purpose, key in config.routes.items():
            item = config.deployments.get(key)
            if item is not None and purpose in PRIVATE_ROUTE_PURPOSES:
                routed.setdefault(item.provider_id, set()).add(purpose)
    out: list[ProcessorOut] = []
    for provider in providers:
        try:
            on_server = classify(provider.base_url, settings) == LlmProviderKind.LOCAL
        except EndpointNotAllowed:
            continue
        out.append(
            ProcessorOut(
                name=provider.name,
                host=_host(provider.base_url),
                on_server=on_server,
                region=provider.cleared_region,
                retention=provider.cleared_retention,
                contract_ref=provider.cleared_contract_ref,
                cleared_since=provider.cleared_at,
                purposes=sorted(routed.get(provider.id, set())),
            )
        )
    served = {p for purposes in routed.values() for p in purposes}
    built_in = sorted(
        LlmRoutePurpose(p.value)
        for p in builtin_purposes(settings)
        if p != LLMPurpose.PLAYGROUND and LlmRoutePurpose(p.value) not in served
    )
    banner = any(
        not p.on_server and p.cleared_since is not None and (seen is None or p.cleared_since > seen)
        for p in out
    )
    return ProcessorsOut(processors=out, built_in=built_in, banner=banner)


@router.post("/privacy/processors/seen", status_code=status.HTTP_204_NO_CONTENT)
async def processors_seen(request: Request, principal: CurrentUser) -> Response:
    async with request.app.state.session_factory() as session, session.begin():
        await session.execute(
            update(User).where(User.id == principal.id).values(processors_seen_at=datetime.now(UTC))
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
