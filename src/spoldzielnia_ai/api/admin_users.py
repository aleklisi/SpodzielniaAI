"""``/admin/users``: accounts, passwords, blocks and roles (chunk 8.4). ADMIN + MFA on every
route (``elevated_router``).

Rules live in ``auth.user_admin``; this module maps their ``UserAdminError`` codes to HTTP
answers and Keycloak failures to 502/503. A temporary password appears only in the response to
the request that set it (``Cache-Control: no-store`` is on every API response). Mutations are
rate limited per admin (``keycloak_admin.mutations_per_minute``), temporary passwords further
(``keycloak_admin.temporary_passwords_per_hour``). The shield's unlock stays at
``POST /admin/users/{id}/unlock`` (``api.admin``, 6.6).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.auth.deps import (
    CurrentUser,
    Principal,
    elevated_router,
    get_session_manager,
    step_up_url,
)
from spoldzielnia_ai.auth.keycloak_admin import IdpUnavailable, KcUser, KeycloakAdminError
from spoldzielnia_ai.auth.user_admin import (
    Actor,
    BlockReason,
    NewAccount,
    PasswordMode,
    UserAdminError,
    UserAdminService,
    UserStatus,
    status_of,
)
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.core.metrics import USER_ADMIN_ACTIONS
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import User

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings

log = get_logger(__name__)

router = elevated_router(Role.ADMIN, prefix="/admin/users", tags=["admin-users"])


# --- Dependencies, errors, limits ---------------------------------------------------------------


def get_user_admin(request: Request) -> UserAdminService:
    state = request.app.state
    # Tests replace Keycloak with an in-memory fake.
    idp = getattr(state, "user_idp", None) or state.keycloak_admin
    sessions = get_session_manager(request)
    return UserAdminService(state.session_factory, idp, state.settings, sessions.revoke_user)


Service = Annotated[UserAdminService, Depends(get_user_admin)]


def _actor(principal: Principal) -> Actor:
    return Actor(
        id=principal.id,
        auth_time=principal.session.auth_time,
        mfa_satisfied=principal.mfa_satisfied,
    )


def _http(request: Request, exc: UserAdminError) -> HTTPException:
    detail: dict[str, object] = {"error": exc.code, **exc.detail}
    if exc.code == "reauthentication_required":
        detail["step_up"] = step_up_url(request.app.state.settings)
    return HTTPException(status_code=exc.status, detail=detail)


async def _run[T](request: Request, action: str, work: Callable[[], Awaitable[T]]) -> T:
    """One admin action: metrics by outcome, rule violations and Keycloak failures as HTTP."""
    try:
        result = await work()
    except UserAdminError as exc:
        USER_ADMIN_ACTIONS.labels(action, exc.code).inc()
        raise _http(request, exc) from None
    except IdpUnavailable as exc:
        USER_ADMIN_ACTIONS.labels(action, "idp_unavailable").inc()
        log.warning("user_admin.idp_unavailable", action=action, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "idp_unavailable"},
            headers={"Retry-After": "10"},
        ) from None
    except KeycloakAdminError as exc:
        USER_ADMIN_ACTIONS.labels(action, "idp_error").inc()
        log.error("user_admin.idp_error", action=action, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"error": "idp_error"}
        ) from None
    USER_ADMIN_ACTIONS.labels(action, "ok").inc()
    return result


async def _limit(request: Request, admin_id: UUID, what: str, limit: int, window: int) -> None:
    """A fixed window per admin in Redis. Fails open if Redis is down (admins only)."""
    redis: Redis = request.app.state.redis
    key = f"useradmin:limit:{what}:{admin_id}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, window)
    except Exception as exc:
        log.warning("user_admin.rate_limit_unavailable", error=type(exc).__name__)
        return
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited"},
            headers={"Retry-After": str(window)},
        )


async def mutation_limit(request: Request, principal: CurrentUser) -> None:
    settings: Settings = request.app.state.settings
    await _limit(
        request, principal.id, "mutations", settings.keycloak_admin.mutations_per_minute, 60
    )


async def _temporary_password_limit(request: Request, principal: Principal) -> None:
    settings: Settings = request.app.state.settings
    per_hour = settings.keycloak_admin.temporary_passwords_per_hour
    await _limit(request, principal.id, "temporary_password", per_hour, 3600)


Mutation = Depends(mutation_limit)


# --- Schemas ------------------------------------------------------------------------------------


class UserOut(BaseModel):
    id: UUID
    username: str | None
    display_name: str | None
    email: str | None
    roles: list[Role]
    status: UserStatus
    blocked_reason: str | None
    locked_reason: str | None
    required_actions: list[str]
    last_login_at: datetime | None
    created_at: datetime
    is_self: bool


class IdpOut(BaseModel):
    """Live from Keycloak."""

    enabled: bool
    email: str | None
    email_verified: bool
    totp: bool
    required_actions: list[str]
    identity_providers: list[str]


class UserDetailOut(UserOut):
    blocked_at: datetime | None
    blocked_by: UUID | None
    blocked_note: str | None
    locked_at: datetime | None
    risk_score: float
    created_by: UUID | None
    # Active admins other than this user: 0 means this is the last one (UI guardrail hint).
    other_active_admins: int
    idp: IdpOut | None


class UserPageOut(BaseModel):
    items: list[UserOut]
    total: int


class CreateUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    # A shape check only; Keycloak validates the address too.
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    roles: list[Role] = Field(default_factory=lambda: [Role.MEMBER], max_length=3)
    password_mode: PasswordMode = PasswordMode.EMAIL


class CreatedOut(BaseModel):
    user: UserOut
    # Shown to the admin once, never stored (U1).
    temporary_password: str | None


class RolesIn(BaseModel):
    roles: list[Role] = Field(max_length=3)


class BlockIn(BaseModel):
    reason: BlockReason
    note: str | None = Field(default=None, max_length=200)


class PasswordResetIn(BaseModel):
    mode: PasswordMode


class PasswordResetOut(BaseModel):
    mode: PasswordMode
    temporary_password: str | None


class PasswordChangeIn(BaseModel):
    sign_out: bool = False


class SignOutOut(BaseModel):
    app_sessions: int


class SyncOut(BaseModel):
    seen: int
    created: int
    updated: int
    blocked: int


def _out(user: User, principal: Principal) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        roles=list(user.roles),
        status=status_of(user),
        blocked_reason=user.blocked_reason,
        locked_reason=user.locked_reason,
        required_actions=list(user.required_actions),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        is_self=user.id == principal.id,
    )


def _idp(kc: KcUser | None) -> IdpOut | None:
    if kc is None:
        return None
    return IdpOut(
        enabled=kc.enabled,
        email=kc.email,
        email_verified=kc.email_verified,
        totp=kc.totp,
        required_actions=list(kc.required_actions),
        identity_providers=list(kc.identity_providers),
    )


# --- Routes -------------------------------------------------------------------------------------
# Static paths before /{user_id}.


@router.get("", response_model=UserPageOut)
async def list_users(
    request: Request,
    principal: CurrentUser,
    service: Service,
    q: Annotated[str, Query(max_length=100)] = "",
    role: Role | None = None,
    status_: Annotated[UserStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UserPageOut:
    page = await _run(
        request,
        "list",
        lambda: service.list(q=q, role=role, status=status_, offset=offset, limit=limit),
    )
    return UserPageOut(items=[_out(u, principal) for u in page.items], total=page.total)


@router.post(
    "", response_model=CreatedOut, status_code=status.HTTP_201_CREATED, dependencies=[Mutation]
)
async def create_user(
    payload: CreateUserIn, request: Request, principal: CurrentUser, service: Service
) -> CreatedOut:
    if payload.password_mode is PasswordMode.TEMPORARY:
        await _temporary_password_limit(request, principal)
    account = NewAccount(
        username=payload.username,
        email=payload.email or None,
        first_name=(payload.first_name or "").strip() or None,
        last_name=(payload.last_name or "").strip() or None,
        roles=frozenset(payload.roles),
        password_mode=payload.password_mode,
    )
    created = await _run(request, "create", lambda: service.create(_actor(principal), account))
    return CreatedOut(
        user=_out(created.user, principal), temporary_password=created.temporary_password
    )


@router.post("/sync", response_model=SyncOut, dependencies=[Mutation])
async def sync_users(request: Request, principal: CurrentUser, service: Service) -> SyncOut:
    report = await _run(request, "sync", lambda: service.sync(_actor(principal)))
    return SyncOut(
        seen=report.seen, created=report.created, updated=report.updated, blocked=report.blocked
    )


@router.get("/{user_id}", response_model=UserDetailOut)
async def get_user(
    user_id: UUID, request: Request, principal: CurrentUser, service: Service
) -> UserDetailOut:
    detail = await _run(request, "get", lambda: service.get(user_id))
    user = detail.user
    async with request.app.state.session_factory() as session:
        admins = await session.scalars(select(User).where(User.roles.contains([Role.ADMIN])))
        others = sum(1 for a in admins if a.id != user.id and not a.refused)
    return UserDetailOut(
        **_out(user, principal).model_dump(),
        blocked_at=user.blocked_at,
        blocked_by=user.blocked_by,
        blocked_note=user.blocked_note,
        locked_at=user.locked_at,
        risk_score=user.risk_score,
        created_by=user.created_by,
        other_active_admins=others,
        idp=_idp(detail.idp),
    )


@router.put("/{user_id}/roles", response_model=UserOut, dependencies=[Mutation])
async def set_roles(
    user_id: UUID, payload: RolesIn, request: Request, principal: CurrentUser, service: Service
) -> UserOut:
    user = await _run(
        request, "roles", lambda: service.set_roles(_actor(principal), user_id, payload.roles)
    )
    return _out(user, principal)


@router.post("/{user_id}/block", response_model=UserOut, dependencies=[Mutation])
async def block_user(
    user_id: UUID, payload: BlockIn, request: Request, principal: CurrentUser, service: Service
) -> UserOut:
    user = await _run(
        request,
        "block",
        lambda: service.block(_actor(principal), user_id, payload.reason, payload.note),
    )
    return _out(user, principal)


@router.post("/{user_id}/unblock", response_model=UserOut, dependencies=[Mutation])
async def unblock_user(
    user_id: UUID, request: Request, principal: CurrentUser, service: Service
) -> UserOut:
    user = await _run(request, "unblock", lambda: service.unblock(_actor(principal), user_id))
    return _out(user, principal)


@router.post("/{user_id}/password-reset", response_model=PasswordResetOut, dependencies=[Mutation])
async def reset_password(
    user_id: UUID,
    payload: PasswordResetIn,
    request: Request,
    principal: CurrentUser,
    service: Service,
) -> PasswordResetOut:
    if payload.mode is PasswordMode.TEMPORARY:
        await _temporary_password_limit(request, principal)
    password = await _run(
        request,
        "password_reset",
        lambda: service.reset_password(_actor(principal), user_id, payload.mode),
    )
    return PasswordResetOut(mode=payload.mode, temporary_password=password)


@router.post("/{user_id}/require-password-change", response_model=UserOut, dependencies=[Mutation])
async def require_password_change(
    user_id: UUID,
    payload: PasswordChangeIn,
    request: Request,
    principal: CurrentUser,
    service: Service,
) -> UserOut:
    user = await _run(
        request,
        "require_password_change",
        lambda: service.require_password_change(
            _actor(principal), user_id, sign_out=payload.sign_out
        ),
    )
    return _out(user, principal)


@router.post("/{user_id}/sign-out", response_model=SignOutOut, dependencies=[Mutation])
async def sign_out_user(
    user_id: UUID, request: Request, principal: CurrentUser, service: Service
) -> SignOutOut:
    ended = await _run(request, "sign_out", lambda: service.sign_out(_actor(principal), user_id))
    return SignOutOut(app_sessions=ended)
