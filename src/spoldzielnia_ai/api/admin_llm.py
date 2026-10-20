"""``/admin/llm``: LLM providers, models, probes and configuration revisions (7.4). ADMIN + MFA
on every route (``elevated_router``).

Every request first imports ``.env`` as revision 1 if nothing is stored yet, so the console
always shows what the system actually runs. Rules (local-only purposes, the cloud ceiling,
endpoint allowlists, probe freshness, no embedding model change) live in
``llm.config_service``; this module maps their ``ConfigError`` codes to HTTP answers. API keys
are accepted, never returned (``key_set`` / ``key_hint`` only). Connection tests and probes are
rate limited per admin (twice ``llm.playground_per_hour``).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
    Role,
)
from spoldzielnia_ai.llm.config_service import (
    LOCAL_ONLY_PURPOSES,
    ConfigError,
    LlmConfigService,
    ModelIn,
    ModelPatch,
    ProviderIn,
    ProviderPatch,
    RevisionView,
    capability_for,
)
from spoldzielnia_ai.llm.endpoints import allowed_endpoints
from spoldzielnia_ai.llm.probes import deployment_for, list_models, probe_model
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot

if TYPE_CHECKING:
    from pydantic import JsonValue
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import LlmModel, LlmProvider
    from spoldzielnia_ai.llm.gateway import ChatTransport

log = get_logger(__name__)

router = elevated_router(Role.ADMIN, prefix="/admin/llm", tags=["admin-llm"])


# --- Dependencies & errors ----------------------------------------------------------------------


async def get_service(request: Request, principal: CurrentUser) -> LlmConfigService:
    state = request.app.state
    settings: Settings = state.settings
    service = LlmConfigService(
        state.session_factory,
        state.cipher,
        settings.llm,
        vector_size=settings.qdrant.vector_size,
        redis=state.redis,
    )
    try:
        await service.ensure_seeded(principal.id)
    except ConfigError as exc:  # pragma: no cover - seeding only skips what is not allowed
        raise _http(exc) from None
    return service


Service = Annotated[LlmConfigService, Depends(get_service)]


def _http(exc: ConfigError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.detail})


async def _rate_limit(request: Request, principal_id: UUID, what: str, per_hour: int) -> None:
    """A fixed one-hour window per admin in Redis. Fails open if Redis is down (admins only)."""
    redis: Redis = request.app.state.redis
    key = f"llm:limit:{what}:{principal_id}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, 3600)
    except Exception as exc:
        log.warning("llm.rate_limit_unavailable", error=type(exc).__name__)
        return
    if count > per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"}
        )


def _transport(request: Request) -> ChatTransport | None:
    """Tests replace the transport of probes and the playground; production builds LiteLLM's."""
    return getattr(request.app.state, "llm_probe_transport", None)


# --- Schemas ------------------------------------------------------------------------------------


class ProviderOut(BaseModel):
    id: UUID
    name: str
    kind: LlmProviderKind
    allowed: bool  # still on the allowlists (they live in the server configuration)
    base_url: str
    litellm_provider: str
    key_set: bool
    key_hint: str | None
    inherit_env_key: bool
    structured_output: str | None
    timeout_seconds: float | None
    disabled: bool
    in_active: bool
    created_at: datetime
    updated_at: datetime


class ModelOut(BaseModel):
    id: UUID
    provider_id: UUID
    model_name: str
    capability: LlmCapability
    temperature: float | None
    max_tokens: int | None
    embed_dimension: int | None
    notes: str | None
    disabled: bool
    in_active: bool
    last_probe_at: datetime | None
    last_probe_ok: bool | None
    last_probe_report: dict[str, Any] | None


class RouteOut(BaseModel):
    model_id: UUID
    name: str
    kind: LlmProviderKind


class RevisionOut(BaseModel):
    number: int
    status: LlmRevisionStatus
    comment: str | None
    based_on: int | None
    rolled_back_from: int | None
    created_by: UUID | None
    created_at: datetime
    activated_by: UUID | None
    activated_at: datetime | None
    routing: dict[LlmRoutePurpose, UUID]
    # Frozen routes of ACTIVE/ARCHIVED revisions (what actually ran).
    routes: dict[LlmRoutePurpose, RouteOut] | None


class PurposeOut(BaseModel):
    purpose: LlmRoutePurpose
    capability: LlmCapability
    local_only: bool


class StatusOut(BaseModel):
    config_source: str
    allow_cloud: bool
    local_endpoints: list[str]
    cloud_hosts: list[str]
    egress_proxy: bool
    vector_size: int
    probe_max_age_hours: int
    active_revision: int | None
    process_revision: int  # what this API process routes by right now (0 = .env)
    purposes: list[PurposeOut]


class DraftIn(BaseModel):
    based_on: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=500)


class RoutingIn(BaseModel):
    routes: dict[LlmRoutePurpose, UUID | None]
    comment: str | None = Field(default=None, max_length=500)


class RollbackIn(BaseModel):
    comment: str | None = Field(default=None, max_length=500)


class DisableIn(BaseModel):
    disabled: bool


class ConnectionOut(BaseModel):
    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    models: list[str] = Field(default_factory=list[str])


class ProbeOut(BaseModel):
    model: ModelOut
    report: dict[str, Any]


def _allowed(provider: LlmProvider, service: LlmConfigService) -> bool:
    try:
        service.kind_of(provider.base_url)
    except ConfigError:
        return False
    return True


def _provider_out(
    provider: LlmProvider, service: LlmConfigService, active: set[UUID]
) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,
        allowed=_allowed(provider, service),
        base_url=provider.base_url,
        litellm_provider=provider.litellm_provider,
        key_set=provider.api_key_ciphertext is not None or provider.inherit_env_key,
        key_hint=provider.api_key_hint,
        inherit_env_key=provider.inherit_env_key,
        structured_output=provider.structured_output,
        timeout_seconds=provider.timeout_seconds,
        disabled=provider.disabled_at is not None,
        in_active=provider.id in active,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _model_out(model: LlmModel, active: set[UUID]) -> ModelOut:
    return ModelOut(
        id=model.id,
        provider_id=model.provider_id,
        model_name=model.model_name,
        capability=model.capability,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        embed_dimension=model.embed_dimension,
        notes=model.notes,
        disabled=model.disabled_at is not None,
        in_active=model.id in active,
        last_probe_at=model.last_probe_at,
        last_probe_ok=model.last_probe_ok,
        last_probe_report=dict(model.last_probe_report) if model.last_probe_report else None,
    )


def _revision_out(view: RevisionView) -> RevisionOut:
    revision = view.revision
    routes: dict[LlmRoutePurpose, RouteOut] | None = None
    if revision.snapshot is not None:
        snapshot = ConfigSnapshot.model_validate(revision.snapshot)
        routes = {
            purpose: RouteOut(model_id=d.model_id, name=d.display_name, kind=d.kind)
            for purpose in snapshot.routes
            if (d := snapshot.deployment_for(purpose)) is not None
        }
    return RevisionOut(
        number=revision.number,
        status=revision.status,
        comment=revision.comment,
        based_on=revision.based_on,
        rolled_back_from=revision.rolled_back_from,
        created_by=revision.created_by,
        created_at=revision.created_at,
        activated_by=revision.activated_by,
        activated_at=revision.activated_at,
        routing=view.routing,
        routes=routes,
    )


async def _active_providers(service: LlmConfigService) -> tuple[set[UUID], set[UUID]]:
    active = await service.active()
    if active is None or active.revision.snapshot is None:
        return set(), set()
    snapshot = ConfigSnapshot.model_validate(active.revision.snapshot)
    deployments = snapshot.deployments.values()
    return {d.provider_id for d in deployments}, {d.model_id for d in deployments}


# --- Status -------------------------------------------------------------------------------------


@router.get("/status", response_model=StatusOut)
async def get_status(request: Request, service: Service) -> StatusOut:
    settings: Settings = request.app.state.settings
    llm = settings.llm
    local, cloud = allowed_endpoints(llm)
    active = await service.active()
    store = getattr(request.app.state, "llm_config", None)
    return StatusOut(
        config_source=llm.config_source,
        allow_cloud=llm.allow_cloud,
        local_endpoints=local,
        cloud_hosts=cloud,
        egress_proxy=llm.egress_proxy_url is not None,
        vector_size=settings.qdrant.vector_size,
        probe_max_age_hours=llm.probe_max_age_hours,
        active_revision=active.revision.number if active else None,
        process_revision=(await store.current()).revision if store is not None else 0,
        purposes=[
            PurposeOut(purpose=p, capability=capability_for(p), local_only=p in LOCAL_ONLY_PURPOSES)
            for p in LlmRoutePurpose
        ],
    )


# --- Providers ----------------------------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
async def get_providers(service: Service) -> list[ProviderOut]:
    active, _ = await _active_providers(service)
    return [_provider_out(p, service, active) for p in await service.providers()]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderIn, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.create_provider(principal.id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    return _provider_out(provider, service, set())


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: UUID, payload: ProviderPatch, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.update_provider(principal.id, provider_id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.post("/providers/{provider_id}/disabled", response_model=ProviderOut)
async def disable_provider(
    provider_id: UUID, payload: DisableIn, service: Service, principal: CurrentUser
) -> ProviderOut:
    try:
        provider = await service.set_provider_disabled(principal.id, provider_id, payload.disabled)
    except ConfigError as exc:
        raise _http(exc) from None
    active, _ = await _active_providers(service)
    return _provider_out(provider, service, active)


@router.post("/providers/{provider_id}/test", response_model=ConnectionOut)
async def test_provider(
    provider_id: UUID, request: Request, service: Service, principal: CurrentUser
) -> ConnectionOut:
    """Reachability, authentication and the models on offer (``GET {base_url}/models``)."""
    settings: Settings = request.app.state.settings
    await _rate_limit(request, principal.id, "probe", settings.llm.playground_per_hour * 2)
    try:
        provider = await service.provider(provider_id)
        kind = service.kind_of(provider.base_url)
    except ConfigError as exc:
        raise _http(exc) from None
    if kind == LlmProviderKind.CLOUD and not settings.llm.allow_cloud:
        raise _http(ConfigError("cloud_not_allowed"))
    client = getattr(request.app.state, "llm_probe_http", None)
    result = await list_models(
        provider.base_url, service.api_key(provider), kind, settings.llm, client
    )
    return ConnectionOut.model_validate(result)


# --- Models -------------------------------------------------------------------------------------


@router.get("/models", response_model=list[ModelOut])
async def get_models(service: Service) -> list[ModelOut]:
    _, active = await _active_providers(service)
    return [_model_out(m, active) for m in await service.models()]


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(payload: ModelIn, service: Service, principal: CurrentUser) -> ModelOut:
    try:
        model = await service.create_model(principal.id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    return _model_out(model, set())


@router.patch("/models/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: UUID, payload: ModelPatch, service: Service, principal: CurrentUser
) -> ModelOut:
    try:
        model = await service.update_model(principal.id, model_id, payload)
    except ConfigError as exc:
        raise _http(exc) from None
    _, active = await _active_providers(service)
    return _model_out(model, active)


@router.post("/models/{model_id}/disabled", response_model=ModelOut)
async def disable_model(
    model_id: UUID, payload: DisableIn, service: Service, principal: CurrentUser
) -> ModelOut:
    try:
        model = await service.set_model_disabled(principal.id, model_id, payload.disabled)
    except ConfigError as exc:
        raise _http(exc) from None
    _, active = await _active_providers(service)
    return _model_out(model, active)


@router.post("/models/{model_id}/probe", response_model=ProbeOut)
async def probe(
    model_id: UUID, request: Request, service: Service, principal: CurrentUser
) -> ProbeOut:
    """Synthetic checks against the model (``llm.probes``); the report is stored on the model
    and gates activation."""
    settings: Settings = request.app.state.settings
    await _rate_limit(request, principal.id, "probe", settings.llm.playground_per_hour * 2)
    try:
        model, provider = await service.model(model_id)
        kind = service.kind_of(provider.base_url)
    except ConfigError as exc:
        raise _http(exc) from None
    if kind == LlmProviderKind.CLOUD and not settings.llm.allow_cloud:
        raise _http(ConfigError("cloud_not_allowed"))
    deployment = deployment_for(
        f"{provider.name}/{model.model_name}",
        model.model_name,
        provider.base_url,
        service.api_key(provider),
        kind,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        structured_output=provider.structured_output,
        timeout_seconds=provider.timeout_seconds,
    )
    report: dict[str, JsonValue] = await probe_model(
        model.capability,
        deployment,
        settings.llm,
        vector_size=settings.qdrant.vector_size,
        transport=_transport(request),
    )
    updated = await service.record_probe(principal.id, model.id, bool(report.get("ok")), report)
    _, active = await _active_providers(service)
    return ProbeOut(model=_model_out(updated, active), report=dict(report))


# --- Revisions ----------------------------------------------------------------------------------


@router.get("/revisions", response_model=list[RevisionOut])
async def get_revisions(service: Service) -> list[RevisionOut]:
    out: list[RevisionOut] = []
    for revision in await service.revisions():
        out.append(_revision_out(await service.revision(revision.number)))
    return out


@router.get("/revisions/{number}", response_model=RevisionOut)
async def get_revision(number: int, service: Service) -> RevisionOut:
    try:
        return _revision_out(await service.revision(number))
    except ConfigError as exc:
        raise _http(exc) from None


@router.post("/revisions", response_model=RevisionOut, status_code=status.HTTP_201_CREATED)
async def create_draft(payload: DraftIn, service: Service, principal: CurrentUser) -> RevisionOut:
    try:
        view = await service.create_draft(principal.id, payload.based_on, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)


@router.put("/revisions/{number}/routing", response_model=RevisionOut)
async def set_routing(
    number: int, payload: RoutingIn, service: Service, principal: CurrentUser
) -> RevisionOut:
    try:
        view = await service.set_routing(principal.id, number, payload.routes, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)


@router.delete("/revisions/{number}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(number: int, service: Service, principal: CurrentUser) -> Response:
    try:
        await service.delete_draft(principal.id, number)
    except ConfigError as exc:
        raise _http(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/revisions/{number}/activate", response_model=RevisionOut)
async def activate(number: int, service: Service, principal: CurrentUser) -> RevisionOut:
    try:
        view = await service.activate(principal.id, number)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)


@router.post("/revisions/{number}/rollback", response_model=RevisionOut)
async def rollback(
    number: int, payload: RollbackIn, service: Service, principal: CurrentUser
) -> RevisionOut:
    try:
        view = await service.rollback(principal.id, number, payload.comment)
    except ConfigError as exc:
        raise _http(exc) from None
    return _revision_out(view)
