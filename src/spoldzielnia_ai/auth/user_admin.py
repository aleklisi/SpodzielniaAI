"""Admin user management (Stage 8, chunk 8.3): accounts, passwords, blocks, roles.

Keycloak holds credentials, realm roles and the enabled flag; ``app_user`` mirrors them for
authorization and the admin list. Every operation checks the guardrails, writes Keycloak first,
then the mirror and an audit event in one transaction, so a Keycloak failure leaves the mirror
untouched. If the mirror fails after Keycloak changed, the next login or ``sync`` reconciles.

Invariants (docs/implementation/08_USER_MANAGEMENT.md §2):

- U1: a temporary password exists only in the Keycloak request and the one response to the
  admin; it is never logged, audited or stored.
- U3: only ``MEMBER``/``EXPERT``/``ADMIN`` are granted (``Role``), and every account is a MEMBER.
- U4: no one removes their own ADMIN role or blocks themselves; the last active ADMIN cannot be
  demoted or blocked (admin rows are locked ``FOR UPDATE`` before counting).
- U5: granting EXPERT/ADMIN, creating such an account and unblocking need a fresh second factor.
- U6: a block disables the Keycloak user, ends its Keycloak sessions and every app session.
- U7: becoming EXPERT/ADMIN without an OTP credential adds the ``CONFIGURE_TOTP`` action.
- U8: an admin block never touches the shield's lock and vice versa (separate columns).
"""

from __future__ import annotations

import re
import secrets
import string
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import func, or_, select

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.auth.keycloak_admin import (
    ActionEmailFailed,
    KcUser,
    KeycloakAdminError,
    PasswordRejected,
    UserExists,
    UserNotFound,
)
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import User

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

    from pydantic import JsonValue
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory

log = get_logger(__name__)

ELEVATED = frozenset({Role.EXPERT, Role.ADMIN})
APP_ROLES = frozenset(r.value for r in Role)
UPDATE_PASSWORD = "UPDATE_PASSWORD"  # noqa: S105 - Keycloak required-action name
CONFIGURE_TOTP = "CONFIGURE_TOTP"
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
# Keycloak's own service accounts are not people and never appear in the console.
SERVICE_ACCOUNT_PREFIX = "service-account-"

_TEMP_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "Il1O0o")
_TEMP_SYMBOLS = "-_.!?"


class BlockReason(StrEnum):
    LEFT_COOPERATIVE = "left_cooperative"
    SECURITY = "security"
    POLICY_VIOLATION = "policy_violation"
    OTHER = "other"
    # Set by `sync` when someone disabled the user in the Keycloak console.
    IDP_DISABLED = "idp_disabled"


class PasswordMode(StrEnum):
    EMAIL = "email"
    TEMPORARY = "temporary"


class UserStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    SHIELD_LOCKED = "shield_locked"
    PENDING_SETUP = "pending_setup"


class UserAdminError(Exception):
    """``code`` names the rule; ``status`` is the HTTP status the API answers with."""

    def __init__(self, code: str, status: int = 409, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


class UserIdp(Protocol):
    """What the service needs from Keycloak (``KeycloakAdmin`` in production)."""

    @property
    def configured(self) -> bool: ...

    async def search_users(
        self, search: str = "", first: int = 0, maximum: int = 100
    ) -> list[KcUser]: ...

    async def get_user(self, user_id: str) -> KcUser: ...

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
        required_actions: Sequence[str],
    ) -> str: ...

    async def update_user(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        required_actions: Sequence[str] | None = None,
    ) -> None: ...

    async def delete_user(self, user_id: str) -> None: ...

    async def realm_roles_of(self, user_id: str) -> set[str]: ...

    async def add_realm_roles(self, user_id: str, names: Sequence[str]) -> None: ...

    async def remove_realm_roles(self, user_id: str, names: Sequence[str]) -> None: ...

    async def set_temporary_password(self, user_id: str, password: str) -> None: ...

    async def send_actions_email(self, user_id: str, actions: Sequence[str]) -> None: ...

    async def logout(self, user_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class Actor:
    """The admin acting. ``auth_time``/``mfa_satisfied`` come from their session."""

    id: UUID
    auth_time: int | None
    mfa_satisfied: bool


@dataclass(frozen=True, slots=True)
class NewAccount:
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    roles: frozenset[Role]
    password_mode: PasswordMode


@dataclass(frozen=True, slots=True)
class Created:
    user: User
    # Only in TEMPORARY mode, shown to the admin once (U1).
    temporary_password: str | None


@dataclass(frozen=True, slots=True)
class Detail:
    user: User
    # Live from Keycloak; None while Keycloak is unreachable (the mirror is still shown).
    idp: KcUser | None


@dataclass(frozen=True, slots=True)
class Page:
    items: list[User]
    total: int


@dataclass(frozen=True, slots=True)
class SyncReport:
    seen: int
    created: int
    updated: int
    blocked: int


def status_of(user: User) -> UserStatus:
    if user.blocked_at is not None:
        return UserStatus.BLOCKED
    if user.is_locked:
        return UserStatus.SHIELD_LOCKED
    if user.required_actions:
        return UserStatus.PENDING_SETUP
    return UserStatus.ACTIVE


def temporary_password(length: int = 16) -> str:
    """Unambiguous characters, with an upper, a lower, a digit and a symbol (any realm policy)."""
    while True:
        body = [secrets.choice(_TEMP_ALPHABET) for _ in range(length - 1)]
        body.insert(secrets.randbelow(length), secrets.choice(_TEMP_SYMBOLS))
        candidate = "".join(body)
        if (
            any(c.isupper() for c in candidate)
            and any(c.islower() for c in candidate)
            and any(c.isdigit() for c in candidate)
        ):
            return candidate


def _with_member(roles: Sequence[Role] | frozenset[Role]) -> frozenset[Role]:
    return frozenset(roles) | {Role.MEMBER}


def _sorted(roles: frozenset[Role] | set[Role]) -> list[Role]:
    return sorted(roles, key=list(Role).index)


class UserAdminService:
    def __init__(
        self,
        factory: SessionFactory,
        idp: UserIdp,
        settings: Settings,
        revoke_sessions: Callable[[UUID], Awaitable[int]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._factory = factory
        self._idp = idp
        self._settings = settings
        self._kc = settings.keycloak_admin
        self._revoke = revoke_sessions
        self._clock = clock

    # --- Guardrails ------------------------------------------------------------------------

    def _require_idp(self) -> None:
        if not self._idp.configured:
            raise UserAdminError("user_admin_unavailable", 503)

    def _require_step_up(self, actor: Actor) -> None:
        """U5: the admin logged in with a second factor within the step-up window."""
        fresh = (
            actor.mfa_satisfied
            and actor.auth_time is not None
            and self._clock() - actor.auth_time <= self._kc.step_up_max_age_seconds
        )
        if not fresh:
            raise UserAdminError(
                "reauthentication_required", 403, max_age_seconds=self._kc.step_up_max_age_seconds
            )

    def _needs_totp(self) -> bool:
        return self._settings.security.mfa_required

    async def _target(self, session: AsyncSession, user_id: UUID) -> User:
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise UserAdminError("user_not_found", 404)
        if user.oidc_issuer != self._settings.oidc.issuer:
            raise UserAdminError("foreign_identity", 409)
        return user

    @staticmethod
    async def _lock_admins(session: AsyncSession) -> list[User]:
        """U4. Locks every ADMIN row, in id order and *before* the target row, so concurrent
        demotions or blocks serialize instead of deadlocking."""
        admins = await session.scalars(
            select(User)
            .where(User.roles.contains([Role.ADMIN]))
            .order_by(User.id)
            .with_for_update()
        )
        return list(admins)

    @staticmethod
    def _other_active_admins(admins: list[User], excluding: UUID) -> int:
        # Rows are re-read under the lock, so roles and blocks reflect committed changes.
        return sum(
            1 for u in admins if u.id != excluding and Role.ADMIN in u.roles and not u.refused
        )

    async def _audit(
        self,
        session: AsyncSession,
        event: EventType,
        actor: Actor,
        user_id: UUID,
        payload: dict[str, JsonValue],
    ) -> None:
        await AuditService(session).record(
            event, actor.id, AuditTarget("app_user", str(user_id)), payload
        )

    @staticmethod
    def _password_managed_elsewhere(kc: KcUser) -> None:
        if kc.identity_providers:
            raise UserAdminError("external_identity", 409, providers=list(kc.identity_providers))

    # --- Reading ---------------------------------------------------------------------------

    async def list(
        self,
        *,
        q: str = "",
        role: Role | None = None,
        status: UserStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Page:
        query = select(User).where(User.oidc_issuer == self._settings.oidc.issuer)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(User.username).like(like),
                    func.lower(User.email).like(like),
                    func.lower(User.display_name).like(like),
                )
            )
        if role is not None:
            query = query.where(User.roles.contains([role]))
        match status:
            case UserStatus.BLOCKED:
                query = query.where(User.blocked_at.is_not(None))
            case UserStatus.SHIELD_LOCKED:
                query = query.where(User.blocked_at.is_(None), User.is_locked)
            case UserStatus.PENDING_SETUP:
                query = query.where(
                    User.blocked_at.is_(None),
                    User.is_locked.is_(False),
                    func.cardinality(User.required_actions) > 0,
                )
            case UserStatus.ACTIVE:
                query = query.where(
                    User.blocked_at.is_(None),
                    User.is_locked.is_(False),
                    func.cardinality(User.required_actions) == 0,
                )
            case None:
                pass
        async with self._factory() as session:
            total = await session.scalar(select(func.count()).select_from(query.subquery()))
            rows = await session.scalars(
                query.order_by(func.lower(func.coalesce(User.username, User.display_name)), User.id)
                .offset(offset)
                .limit(limit)
            )
            return Page(list(rows), total or 0)

    async def get(self, user_id: UUID) -> Detail:
        async with self._factory() as session:
            user = await session.get(User, user_id)
        if user is None or user.oidc_issuer != self._settings.oidc.issuer:
            raise UserAdminError("user_not_found", 404)
        idp: KcUser | None = None
        if self._idp.configured:
            try:
                idp = await self._idp.get_user(user.oidc_subject)
            except UserNotFound:
                raise UserAdminError("user_missing_in_idp", 409) from None
            except KeycloakAdminError as exc:
                log.warning("user_admin.idp_read_failed", error=type(exc).__name__)
        return Detail(user, idp)

    # --- Create ----------------------------------------------------------------------------

    async def create(self, actor: Actor, account: NewAccount) -> Created:
        self._require_idp()
        username = account.username.strip().lower()
        if not USERNAME.fullmatch(username) or username.startswith(SERVICE_ACCOUNT_PREFIX):
            raise UserAdminError("invalid_username", 422)
        roles = _with_member(account.roles)
        elevated = bool(roles & ELEVATED)
        if elevated:
            self._require_step_up(actor)
        if account.password_mode is PasswordMode.EMAIL and not account.email:
            raise UserAdminError("email_required", 422)

        actions = [UPDATE_PASSWORD]
        if elevated and self._needs_totp():
            actions.append(CONFIGURE_TOTP)
        try:
            kc_id = await self._idp.create_user(
                username=username,
                email=account.email,
                first_name=account.first_name,
                last_name=account.last_name,
                required_actions=actions,
            )
        except UserExists:
            raise UserAdminError("user_exists", 409) from None

        password: str | None = None
        try:
            await self._idp.add_realm_roles(kc_id, [r.value for r in _sorted(roles)])
            if account.password_mode is PasswordMode.TEMPORARY:
                password = temporary_password()
                await self._idp.set_temporary_password(kc_id, password)
            else:
                await self._idp.send_actions_email(kc_id, [UPDATE_PASSWORD])
            async with self._factory() as session, session.begin():
                user = User(
                    id=uuid7(),
                    oidc_issuer=self._settings.oidc.issuer,
                    oidc_subject=kc_id,
                    username=username,
                    email=account.email,
                    display_name=(
                        " ".join(p for p in (account.first_name, account.last_name) if p) or None
                    ),
                    roles=_sorted(roles),
                    created_by=actor.id,
                    required_actions=actions,
                )
                session.add(user)
                await session.flush()
                await self._audit(
                    session,
                    EventType.USER_CREATED,
                    actor,
                    user.id,
                    {
                        "username": username,
                        "roles": [r.value for r in _sorted(roles)],
                        "password_mode": account.password_mode.value,
                    },
                )
        except BaseException as exc:
            # Undo the half-created Keycloak user so the username stays free for a retry.
            try:
                await self._idp.delete_user(kc_id)
            except KeycloakAdminError:
                log.error("user_admin.create_rollback_failed", username=username)
            if isinstance(exc, ActionEmailFailed):
                raise UserAdminError("email_failed", 502) from None
            if isinstance(exc, PasswordRejected):
                raise UserAdminError("password_policy", 502, reason=exc.reason) from None
            raise
        return Created(user, password)

    # --- Roles -----------------------------------------------------------------------------

    async def set_roles(self, actor: Actor, user_id: UUID, requested: Sequence[Role]) -> User:
        self._require_idp()
        roles = _with_member(requested)
        async with self._factory() as session, session.begin():
            admins = await self._lock_admins(session)
            user = await self._target(session, user_id)
            before = frozenset(user.roles)
            granted, removed = roles - before, before - roles
            if not granted and not removed:
                return user
            if Role.ADMIN in removed:
                if user.id == actor.id:
                    raise UserAdminError("cannot_demote_self")
                if not user.refused and self._other_active_admins(admins, user.id) == 0:
                    raise UserAdminError("last_admin")
            if granted & ELEVATED:
                self._require_step_up(actor)

            kc_id = user.oidc_subject
            current = await self._idp.realm_roles_of(kc_id) & APP_ROLES
            want = {r.value for r in roles}
            await self._idp.add_realm_roles(kc_id, sorted(want - current))
            await self._idp.remove_realm_roles(kc_id, sorted(current - want))
            actions = list(user.required_actions)
            if roles & ELEVATED and self._needs_totp():
                kc = await self._idp.get_user(kc_id)
                if not kc.totp and CONFIGURE_TOTP not in kc.required_actions:
                    actions = [*kc.required_actions, CONFIGURE_TOTP]
                    await self._idp.update_user(kc_id, required_actions=actions)

            user.roles = _sorted(roles)
            user.required_actions = actions
            await self._audit(
                session,
                EventType.USER_ROLES_CHANGED,
                actor,
                user.id,
                {
                    "before": [r.value for r in _sorted(set(before))],
                    "after": [r.value for r in _sorted(roles)],
                    "granted": [r.value for r in _sorted(set(granted))],
                    "removed": [r.value for r in _sorted(set(removed))],
                },
            )
        return user

    # --- Block -----------------------------------------------------------------------------

    async def block(
        self, actor: Actor, user_id: UUID, reason: BlockReason, note: str | None = None
    ) -> User:
        self._require_idp()
        if reason is BlockReason.IDP_DISABLED:
            raise UserAdminError("invalid_reason", 422)
        async with self._factory() as session, session.begin():
            admins = await self._lock_admins(session)
            user = await self._target(session, user_id)
            if user.id == actor.id:
                raise UserAdminError("cannot_block_self")
            if user.blocked_at is not None:
                raise UserAdminError("already_blocked")
            if (
                Role.ADMIN in user.roles
                and not user.refused
                and self._other_active_admins(admins, user.id) == 0
            ):
                raise UserAdminError("last_admin")
            await self._idp.update_user(user.oidc_subject, enabled=False)
            await self._idp.logout(user.oidc_subject)
            user.blocked_at = func.now()  # pyright: ignore[reportAttributeAccessIssue]
            user.blocked_by = actor.id
            user.blocked_reason = reason.value
            user.blocked_note = (note or "").strip()[:200] or None
            await self._audit(
                session, EventType.USER_BLOCKED, actor, user.id, {"reason": reason.value}
            )
        await self._revoke(user_id)
        return await self._reload(user_id)

    async def unblock(self, actor: Actor, user_id: UUID) -> User:
        self._require_idp()
        self._require_step_up(actor)
        async with self._factory() as session, session.begin():
            user = await self._target(session, user_id)
            if user.blocked_at is None:
                raise UserAdminError("not_blocked")
            await self._idp.update_user(user.oidc_subject, enabled=True)
            previous = user.blocked_reason
            user.blocked_at = None
            user.blocked_by = None
            user.blocked_reason = None
            user.blocked_note = None
            await self._audit(
                session,
                EventType.USER_UNBLOCKED,
                actor,
                user.id,
                # The shield's lock is a separate decision (6.6 unlock), never cleared here.
                {"previous_reason": previous, "shield_locked": user.is_locked},
            )
        return user

    # --- Passwords and sessions --------------------------------------------------------------

    async def reset_password(self, actor: Actor, user_id: UUID, mode: PasswordMode) -> str | None:
        """Returns the temporary password (TEMPORARY mode) for the admin to hand over, once."""
        self._require_idp()
        async with self._factory() as session, session.begin():
            user = await self._target(session, user_id)
            kc = await self._idp.get_user(user.oidc_subject)
            self._password_managed_elsewhere(kc)
            password: str | None = None
            if mode is PasswordMode.TEMPORARY:
                password = temporary_password()
                try:
                    await self._idp.set_temporary_password(user.oidc_subject, password)
                except PasswordRejected as exc:
                    raise UserAdminError("password_policy", 502, reason=exc.reason) from None
                actions = sorted({*kc.required_actions, UPDATE_PASSWORD})
            else:
                if not kc.email:
                    raise UserAdminError("email_required", 422)
                try:
                    await self._idp.send_actions_email(user.oidc_subject, [UPDATE_PASSWORD])
                except ActionEmailFailed:
                    raise UserAdminError("email_failed", 502) from None
                actions = list(kc.required_actions)
            await self._idp.logout(user.oidc_subject)
            user.required_actions = actions
            await self._audit(
                session, EventType.USER_PASSWORD_RESET, actor, user.id, {"mode": mode.value}
            )
        await self._revoke(user_id)
        return password

    async def require_password_change(self, actor: Actor, user_id: UUID, *, sign_out: bool) -> User:
        self._require_idp()
        async with self._factory() as session, session.begin():
            user = await self._target(session, user_id)
            kc = await self._idp.get_user(user.oidc_subject)
            self._password_managed_elsewhere(kc)
            actions = list(kc.required_actions)
            if UPDATE_PASSWORD not in actions:
                actions.append(UPDATE_PASSWORD)
                await self._idp.update_user(user.oidc_subject, required_actions=actions)
            if sign_out:
                await self._idp.logout(user.oidc_subject)
            user.required_actions = actions
            await self._audit(
                session,
                EventType.USER_PASSWORD_CHANGE_REQUIRED,
                actor,
                user.id,
                {"sign_out": sign_out},
            )
        if sign_out:
            await self._revoke(user_id)
        return user

    async def sign_out(self, actor: Actor, user_id: UUID) -> int:
        self._require_idp()
        async with self._factory() as session, session.begin():
            user = await self._target(session, user_id)
            await self._idp.logout(user.oidc_subject)
            ended = await self._revoke(user_id)
            await self._audit(
                session, EventType.USER_SESSIONS_REVOKED, actor, user.id, {"app_sessions": ended}
            )
        return ended

    # --- Reconciliation ----------------------------------------------------------------------

    async def sync(self, actor: Actor | None, page_size: int = 100) -> SyncReport:
        """U10: bring the mirror in line with Keycloak (users created in its console, roles or
        the enabled flag changed there). Keycloak wins for roles and profile; a user disabled in
        Keycloak is blocked here (``idp_disabled``). An app block is never lifted by a sync."""
        self._require_idp()
        seen = created = updated = blocked = 0
        first = 0
        issuer = self._settings.oidc.issuer
        while True:
            batch = await self._idp.search_users(first=first, maximum=page_size)
            for kc in batch:
                if kc.username.startswith(SERVICE_ACCOUNT_PREFIX):
                    continue
                seen += 1
                roles = _sorted(
                    {Role(r) for r in await self._idp.realm_roles_of(kc.id) if r in APP_ROLES}
                )
                async with self._factory() as session, session.begin():
                    user = await session.scalar(
                        select(User)
                        .where(User.oidc_issuer == issuer, User.oidc_subject == kc.id)
                        .with_for_update()
                    )
                    if user is None:
                        user = User(
                            id=uuid7(),
                            oidc_issuer=issuer,
                            oidc_subject=kc.id,
                            username=kc.username,
                            email=kc.email if kc.email_verified else None,
                            display_name=kc.display_name or kc.username,
                            roles=roles,
                            required_actions=list(kc.required_actions),
                        )
                        session.add(user)
                        created += 1
                    else:
                        changed = (
                            user.username != kc.username
                            or list(user.roles) != roles
                            or list(user.required_actions) != list(kc.required_actions)
                        )
                        user.username = kc.username
                        user.roles = roles
                        user.required_actions = list(kc.required_actions)
                        if kc.email_verified and kc.email:
                            user.email = kc.email
                        updated += int(changed)
                    if not kc.enabled and user.blocked_at is None:
                        user.blocked_at = func.now()  # pyright: ignore[reportAttributeAccessIssue]
                        user.blocked_reason = BlockReason.IDP_DISABLED.value
                        user.blocked_by = actor.id if actor else None
                        blocked += 1
            if len(batch) < page_size:
                break
            first += page_size
        report = SyncReport(seen, created, updated, blocked)
        if actor is not None:
            async with self._factory() as session, session.begin():
                await AuditService(session).record(
                    EventType.USERS_SYNCED,
                    actor.id,
                    None,
                    {"seen": seen, "created": created, "updated": updated, "blocked": blocked},
                )
        return report

    async def _reload(self, user_id: UUID) -> User:
        async with self._factory() as session:
            user = await session.get(User, user_id)
        if user is None:  # pragma: no cover - rows are never deleted
            raise UserAdminError("user_not_found", 404)
        return user
