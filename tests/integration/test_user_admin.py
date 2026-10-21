"""Chunk 8.3: ``UserAdminService`` against Postgres with an in-memory Keycloak.

Covers the invariants of docs/implementation/08_USER_MANAGEMENT.md §2: U1 (no password in audit
or logs), U3 (app roles only, MEMBER always), U4 (self and last-admin guards, concurrent
demotions), U5 (step-up), U6 (block ends every session), U7 (TOTP on promotion), U8 (admin block
and shield lock stay apart), U10 (sync) and the rollback of half-finished Keycloak changes.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine
from structlog.testing import capture_logs

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.auth.keycloak_admin import KeycloakAdminError
from spoldzielnia_ai.auth.user_admin import (
    Actor,
    BlockReason,
    NewAccount,
    PasswordMode,
    UserAdminError,
    UserAdminService,
    UserStatus,
    status_of,
    temporary_password,
)
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import SecurityAuditLog, User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.keycloak import FakeUserIdp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from spoldzielnia_ai.core.settings import Settings

pytestmark = pytest.mark.integration

NOW = 1_800_000_000.0


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def idp() -> FakeUserIdp:
    return FakeUserIdp()


class Revocations:
    def __init__(self) -> None:
        self.users: list[UUID] = []

    async def __call__(self, user_id: UUID) -> int:
        self.users.append(user_id)
        return 2


@pytest.fixture
def revoked() -> Revocations:
    return Revocations()


@pytest.fixture
def service(
    factory: SessionFactory, idp: FakeUserIdp, settings: Settings, revoked: Revocations
) -> UserAdminService:
    return UserAdminService(factory, idp, settings, revoked, clock=lambda: NOW)


def _actor(user: User, *, fresh: bool = True) -> Actor:
    return Actor(id=user.id, auth_time=int(NOW) - (60 if fresh else 3600), mfa_satisfied=True)


async def _seed(
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    username: str,
    *roles: Role,
    totp: bool = False,
    **columns: Any,
) -> User:
    """A user who exists in Keycloak and has logged in once (mirror row)."""
    all_roles = sorted({Role.MEMBER, *roles}, key=list(Role).index)
    kc = idp.add(username, *(r.value for r in all_roles), totp=totp)
    user = User(
        id=uuid7(),
        oidc_issuer=settings.oidc.issuer,
        oidc_subject=kc.id,
        username=username,
        display_name=username.title(),
        roles=all_roles,
        **columns,
    )
    async with factory() as session, session.begin():
        session.add(user)
    return user


@pytest.fixture
async def admin(factory: SessionFactory, idp: FakeUserIdp, settings: Settings) -> User:
    return await _seed(factory, idp, settings, "admin1", Role.ADMIN, totp=True)


async def _row(factory: SessionFactory, user_id: UUID) -> User:
    async with factory() as session:
        user = await session.get(User, user_id)
    assert user is not None
    return user


async def _audit(factory: SessionFactory) -> list[SecurityAuditLog]:
    async with factory() as session:
        return list(await session.scalars(select(SecurityAuditLog).order_by(SecurityAuditLog.id)))


def _account(
    username: str, *roles: Role, mode: PasswordMode = PasswordMode.TEMPORARY
) -> NewAccount:
    return NewAccount(
        username=username,
        email=f"{username}@example.test",
        first_name="Nowa",
        last_name="Osoba",
        roles=frozenset(roles),
        password_mode=mode,
    )


# --- Create ---------------------------------------------------------------------------------


async def test_create_with_a_temporary_password(
    service: UserAdminService, factory: SessionFactory, idp: FakeUserIdp, admin: User
) -> None:
    with capture_logs() as logs:
        created = await service.create(_actor(admin), _account("Nowa.Osoba"))

    password = created.temporary_password
    assert password is not None
    assert len(password) == 16
    kc_id = created.user.oidc_subject
    assert idp.passwords[kc_id] == password
    assert idp.roles[kc_id] >= {"MEMBER"}
    assert "EXPERT" not in idp.roles[kc_id]
    assert idp.users[kc_id].required_actions == ("UPDATE_PASSWORD",)

    row = await _row(factory, created.user.id)
    assert row.username == "nowa.osoba"  # Keycloak usernames are lower case
    assert row.roles == [Role.MEMBER]
    assert row.created_by == admin.id
    assert status_of(row) is UserStatus.PENDING_SETUP

    [event] = await _audit(factory)
    assert event.event_type == EventType.USER_CREATED
    assert event.payload == {
        "username": "nowa.osoba",
        "roles": ["MEMBER"],
        "password_mode": "temporary",
    }
    assert password not in repr(event.payload)  # U1
    assert password not in repr(logs)


async def test_create_by_email_link(
    service: UserAdminService, idp: FakeUserIdp, admin: User
) -> None:
    created = await service.create(_actor(admin), _account("ola", mode=PasswordMode.EMAIL))
    assert created.temporary_password is None
    assert idp.emails_sent == [(created.user.oidc_subject, ("UPDATE_PASSWORD",))]

    no_address = dataclasses.replace(_account("ewa", mode=PasswordMode.EMAIL), email=None)
    with pytest.raises(UserAdminError, match="email_required"):
        await service.create(_actor(admin), no_address)


async def test_a_failed_email_undoes_the_keycloak_user(
    service: UserAdminService, factory: SessionFactory, idp: FakeUserIdp, admin: User
) -> None:
    idp.fail_on = "send_actions_email"
    with pytest.raises(UserAdminError) as raised:
        await service.create(_actor(admin), _account("ola", mode=PasswordMode.EMAIL))
    assert (raised.value.code, raised.value.status) == ("email_failed", 502)
    assert [u.username for u in idp.users.values()] == ["admin1"]
    async with factory() as session:
        assert await session.scalar(select(User).where(User.username == "ola")) is None


async def test_create_guards(service: UserAdminService, idp: FakeUserIdp, admin: User) -> None:
    for bad in ("ab", "with space", "service-account-x", "x" * 80):
        with pytest.raises(UserAdminError, match="invalid_username"):
            await service.create(_actor(admin), _account(bad))
    await service.create(_actor(admin), _account("ola"))
    with pytest.raises(UserAdminError, match="user_exists"):
        await service.create(_actor(admin), _account("ola"))
    # U5: an elevated account needs a fresh second factor; nothing reaches Keycloak without it.
    with pytest.raises(UserAdminError) as raised:
        await service.create(_actor(admin, fresh=False), _account("ekspert", Role.EXPERT))
    assert (raised.value.code, raised.value.status) == ("reauthentication_required", 403)
    assert all(u.username != "ekspert" for u in idp.users.values())


async def test_an_elevated_account_must_enrol_totp(
    service: UserAdminService, idp: FakeUserIdp, admin: User
) -> None:
    created = await service.create(_actor(admin), _account("ekspert", Role.EXPERT))
    kc_id = created.user.oidc_subject
    assert idp.roles[kc_id] >= {"MEMBER", "EXPERT"}
    assert set(idp.users[kc_id].required_actions) == {"UPDATE_PASSWORD", "CONFIGURE_TOTP"}


async def test_without_mfa_no_totp_enrolment_is_forced(
    factory: SessionFactory, idp: FakeUserIdp, settings: Settings, revoked: Revocations, admin: User
) -> None:
    relaxed = settings.model_copy(
        update={"security": settings.security.model_copy(update={"mfa_required": False})}
    )
    service = UserAdminService(factory, idp, relaxed, revoked, clock=lambda: NOW)
    created = await service.create(_actor(admin), _account("ekspert", Role.EXPERT))
    assert idp.users[created.user.oidc_subject].required_actions == ("UPDATE_PASSWORD",)


# --- Roles ----------------------------------------------------------------------------------


async def test_granting_expert_needs_step_up_and_totp(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")
    with pytest.raises(UserAdminError, match="reauthentication_required"):
        await service.set_roles(_actor(admin, fresh=False), member.id, [Role.EXPERT])
    assert idp.roles[member.oidc_subject] == {"default-roles-spoldzielnia", "MEMBER"}

    await service.set_roles(_actor(admin), member.id, [Role.EXPERT])

    assert idp.roles[member.oidc_subject] >= {"MEMBER", "EXPERT"}
    assert "CONFIGURE_TOTP" in idp.users[member.oidc_subject].required_actions  # U7
    row = await _row(factory, member.id)
    assert row.roles == [Role.MEMBER, Role.EXPERT]  # MEMBER is never removed
    event = (await _audit(factory))[-1]
    assert event.event_type == EventType.USER_ROLES_CHANGED
    assert event.payload == {
        "before": ["MEMBER"],
        "after": ["MEMBER", "EXPERT"],
        "granted": ["EXPERT"],
        "removed": [],
    }

    # Removing a role needs no step-up.
    await service.set_roles(_actor(admin, fresh=False), member.id, [])
    assert (await _row(factory, member.id)).roles == [Role.MEMBER]
    assert idp.roles[member.oidc_subject] == {"default-roles-spoldzielnia", "MEMBER"}


async def test_a_user_with_totp_gets_no_enrolment_action(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1", totp=True)
    await service.set_roles(_actor(admin), member.id, [Role.EXPERT])
    assert idp.users[member.oidc_subject].required_actions == ()


async def test_admins_cannot_lock_out_the_organisation(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    with pytest.raises(UserAdminError, match="cannot_demote_self"):
        await service.set_roles(_actor(admin), admin.id, [])
    with pytest.raises(UserAdminError, match="cannot_block_self"):
        await service.block(_actor(admin), admin.id, BlockReason.OTHER)

    other = await _seed(factory, idp, settings, "admin2", Role.ADMIN)
    await service.block(_actor(admin), other.id, BlockReason.SECURITY)
    # admin1 is now the only active admin: another admin can't demote or block them, and a
    # blocked admin doesn't count. Demoting the blocked admin is fine.
    await service.set_roles(_actor(admin), other.id, [])
    third = await _seed(factory, idp, settings, "admin3", Role.ADMIN)
    await service.set_roles(_actor(third), admin.id, [])
    with pytest.raises(UserAdminError, match="last_admin"):
        await service.set_roles(_actor(admin, fresh=True), third.id, [])
    with pytest.raises(UserAdminError, match="last_admin"):
        await service.block(_actor(admin), third.id, BlockReason.OTHER)


async def test_two_admins_demoting_each_other_at_once(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    other = await _seed(factory, idp, settings, "admin2", Role.ADMIN)
    idp.delay = 0.2  # both transactions are open at the same time
    results = await asyncio.gather(
        service.set_roles(_actor(admin), other.id, []),
        service.set_roles(_actor(other), admin.id, []),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, UserAdminError)]
    assert len(errors) == 1
    assert errors[0].code == "last_admin"
    async with factory() as session:
        admins = await session.scalars(select(User).where(User.roles.contains([Role.ADMIN])))
        assert len(list(admins)) == 1


async def test_a_keycloak_failure_leaves_the_mirror_unchanged(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")
    idp.fail_on = "add_realm_roles"
    with pytest.raises(KeycloakAdminError):
        await service.set_roles(_actor(admin), member.id, [Role.EXPERT])
    assert (await _row(factory, member.id)).roles == [Role.MEMBER]
    assert all(e.event_type != EventType.USER_ROLES_CHANGED for e in await _audit(factory))


# --- Block ----------------------------------------------------------------------------------


async def test_block_ends_every_session_and_unblock_needs_step_up(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    revoked: Revocations,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")

    blocked = await service.block(_actor(admin), member.id, BlockReason.LEFT_COOPERATIVE, " bye ")

    assert idp.users[member.oidc_subject].enabled is False  # no new logins
    assert idp.logouts == [member.oidc_subject]  # IdP sessions ended
    assert revoked.users == [member.id]  # app sessions ended
    assert blocked.blocked_at is not None
    assert (blocked.blocked_by, blocked.blocked_reason, blocked.blocked_note) == (
        admin.id,
        "left_cooperative",
        "bye",
    )
    assert status_of(blocked) is UserStatus.BLOCKED
    with pytest.raises(UserAdminError, match="already_blocked"):
        await service.block(_actor(admin), member.id, BlockReason.OTHER)

    with pytest.raises(UserAdminError, match="reauthentication_required"):
        await service.unblock(_actor(admin, fresh=False), member.id)
    unblocked = await service.unblock(_actor(admin), member.id)
    assert idp.users[member.oidc_subject].enabled is True
    assert unblocked.blocked_at is None
    assert not unblocked.refused
    with pytest.raises(UserAdminError, match="not_blocked"):
        await service.unblock(_actor(admin), member.id)
    types = [e.event_type for e in await _audit(factory)]
    assert types == [EventType.USER_BLOCKED, EventType.USER_UNBLOCKED]


async def test_admin_block_and_shield_lock_stay_apart(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    member = await _seed(
        factory, idp, settings, "member1", is_locked=True, locked_reason="coverage_7d"
    )
    await service.block(_actor(admin), member.id, BlockReason.SECURITY)
    unblocked = await service.unblock(_actor(admin), member.id)
    assert unblocked.is_locked  # U8: the shield's lock is a separate decision
    assert unblocked.locked_reason == "coverage_7d"
    assert status_of(unblocked) is UserStatus.SHIELD_LOCKED
    assert (await _audit(factory))[-1].payload == {
        "previous_reason": "security",
        "shield_locked": True,
    }


# --- Passwords and sessions -------------------------------------------------------------------


async def test_reset_password_temporary_and_by_email(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    revoked: Revocations,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")

    password = await service.reset_password(_actor(admin), member.id, PasswordMode.TEMPORARY)

    assert password is not None
    assert idp.passwords[member.oidc_subject] == password
    assert idp.logouts == [member.oidc_subject]
    assert revoked.users == [member.id]
    assert (await _row(factory, member.id)).required_actions == ["UPDATE_PASSWORD"]
    event = (await _audit(factory))[-1]
    assert (event.event_type, event.payload) == (
        EventType.USER_PASSWORD_RESET,
        {"mode": "temporary"},
    )

    assert await service.reset_password(_actor(admin), member.id, PasswordMode.EMAIL) is None
    assert idp.emails_sent == [(member.oidc_subject, ("UPDATE_PASSWORD",))]

    idp.fail_on = "send_actions_email"
    with pytest.raises(UserAdminError, match="email_failed"):
        await service.reset_password(_actor(admin), member.id, PasswordMode.EMAIL)


async def test_password_actions_refuse_brokered_users(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")
    idp.users[member.oidc_subject] = dataclasses.replace(
        idp.users[member.oidc_subject], identity_providers=("google",)
    )
    for call in (
        service.reset_password(_actor(admin), member.id, PasswordMode.TEMPORARY),
        service.require_password_change(_actor(admin), member.id, sign_out=False),
    ):
        with pytest.raises(UserAdminError, match="external_identity"):
            await call
    # Blocking and signing out still work for them.
    assert await service.sign_out(_actor(admin), member.id) == 2


async def test_require_password_change(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    revoked: Revocations,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")

    await service.require_password_change(_actor(admin), member.id, sign_out=False)
    assert idp.users[member.oidc_subject].required_actions == ("UPDATE_PASSWORD",)
    assert (idp.logouts, revoked.users) == ([], [])

    await service.require_password_change(_actor(admin), member.id, sign_out=True)
    assert idp.users[member.oidc_subject].required_actions == ("UPDATE_PASSWORD",)  # once
    assert (idp.logouts, revoked.users) == ([member.oidc_subject], [member.id])
    payloads = [e.payload for e in await _audit(factory)]
    assert payloads == [{"sign_out": False}, {"sign_out": True}]


async def test_sign_out_everywhere(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    revoked: Revocations,
    admin: User,
) -> None:
    member = await _seed(factory, idp, settings, "member1")
    assert await service.sign_out(_actor(admin), member.id) == 2
    assert idp.logouts == [member.oidc_subject]
    assert revoked.users == [member.id]
    event = (await _audit(factory))[-1]
    assert (event.event_type, event.payload) == (
        EventType.USER_SESSIONS_REVOKED,
        {"app_sessions": 2},
    )


# --- Reading, sync, availability ----------------------------------------------------------------


async def test_list_filters(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    expert = await _seed(factory, idp, settings, "ekspert", Role.EXPERT)
    blocked = await _seed(factory, idp, settings, "zablokowany")
    await service.block(_actor(admin), blocked.id, BlockReason.OTHER)
    locked = await _seed(factory, idp, settings, "chroniony", is_locked=True)
    pending = await _seed(factory, idp, settings, "nowy", required_actions=["UPDATE_PASSWORD"])

    def names(page: Any) -> list[str]:
        return [u.username for u in page.items]

    assert names(await service.list()) == ["admin1", "chroniony", "ekspert", "nowy", "zablokowany"]
    assert (await service.list(limit=2)).total == 5
    assert names(await service.list(q="EKS")) == ["ekspert"]
    assert names(await service.list(role=Role.EXPERT)) == [expert.username]
    assert names(await service.list(status=UserStatus.BLOCKED)) == [blocked.username]
    assert names(await service.list(status=UserStatus.SHIELD_LOCKED)) == [locked.username]
    assert names(await service.list(status=UserStatus.PENDING_SETUP)) == [pending.username]
    assert names(await service.list(status=UserStatus.ACTIVE)) == ["admin1", "ekspert"]


async def test_detail_reads_keycloak_live(
    service: UserAdminService, idp: FakeUserIdp, admin: User
) -> None:
    detail = await service.get(admin.id)
    assert detail.idp is not None
    assert detail.idp.totp is True
    idp.fail_on = "get_user"
    assert (await service.get(admin.id)).idp is None  # the mirror is still shown


async def test_sync_mirrors_keycloak(
    service: UserAdminService,
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    admin: User,
) -> None:
    console = idp.add("z-konsoli", "MEMBER", "EXPERT")
    disabled = idp.add("wylaczony", "MEMBER", enabled=False)
    idp.add("service-account-spoldzielnia-admin")
    blocked_here = await _seed(factory, idp, settings, "zablokowany")
    await service.block(_actor(admin), blocked_here.id, BlockReason.OTHER)
    # Someone re-enabled the user in the Keycloak console.
    idp.users[blocked_here.oidc_subject] = dataclasses.replace(
        idp.users[blocked_here.oidc_subject], enabled=True
    )
    idp.roles[admin.oidc_subject].add("EXPERT")

    report = await service.sync(_actor(admin))

    assert (report.seen, report.created, report.blocked) == (4, 2, 1)
    async with factory() as session:
        rows = {u.username: u for u in await session.scalars(select(User))}
    assert "service-account-spoldzielnia-admin" not in rows
    assert rows["z-konsoli"].oidc_subject == console.id
    assert rows["z-konsoli"].roles == [Role.MEMBER, Role.EXPERT]
    assert rows["wylaczony"].blocked_reason == "idp_disabled"
    assert rows["wylaczony"].oidc_subject == disabled.id
    assert rows["zablokowany"].blocked_at is not None  # a sync never lifts an app block
    assert rows["admin1"].roles == [Role.MEMBER, Role.EXPERT, Role.ADMIN]
    assert (await _audit(factory))[-1].event_type == EventType.USERS_SYNCED

    again = await service.sync(_actor(admin))
    assert (again.created, again.updated, again.blocked) == (0, 0, 0)


async def test_unconfigured_keycloak_and_foreign_users(
    factory: SessionFactory,
    idp: FakeUserIdp,
    settings: Settings,
    revoked: Revocations,
    admin: User,
) -> None:
    off = UserAdminService(
        factory, FakeUserIdp(configured=False), settings, revoked, clock=lambda: NOW
    )
    with pytest.raises(UserAdminError) as raised:
        await off.sign_out(_actor(admin), admin.id)
    assert (raised.value.code, raised.value.status) == ("user_admin_unavailable", 503)

    service = UserAdminService(factory, idp, settings, revoked, clock=lambda: NOW)
    foreign = await _seed(factory, idp, settings, "obcy")
    async with factory() as session, session.begin():
        await session.execute(
            update(User).where(User.id == foreign.id).values(oidc_issuer="https://other.example")
        )
    with pytest.raises(UserAdminError, match="foreign_identity"):
        await service.sign_out(_actor(admin), foreign.id)
    with pytest.raises(UserAdminError, match="user_not_found"):
        await service.get(foreign.id)


def test_temporary_passwords_are_strong_and_unambiguous() -> None:
    samples = {temporary_password() for _ in range(200)}
    assert len(samples) == 200
    for password in samples:
        assert len(password) == 16
        assert not set(password) & set("Il1O0o")
        assert any(c in "-_.!?" for c in password)
