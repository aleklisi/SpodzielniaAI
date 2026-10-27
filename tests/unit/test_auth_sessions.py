"""Chunk 1.6: cookie sessions, refresh rotation + reuse detection, CSRF, RBAC and MFA guards.

Runs the real app against a fake IdP (respx), fakeredis and in-memory users.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import respx
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient

from spoldzielnia_ai.auth.cookies import CSRF_COOKIE, CSRF_HEADER, SID_COOKIE
from spoldzielnia_ai.auth.crypto import digest
from spoldzielnia_ai.auth.deps import (
    CurrentUser,
    elevated_router,
    get_user_directory,
    get_user_provisioner,
    is_mfa_satisfied,
)
from spoldzielnia_ai.auth.provisioning import ProvisionedUser
from spoldzielnia_ai.auth.sessions import SessionData, SessionManager, SessionStore
from spoldzielnia_ai.auth.users import UserRecord
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.main import create_app
from tests.fixtures.oidc import LOGOUT_URL, FakeIdP

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from fastapi import FastAPI
    from httpx import Response
    from pydantic import JsonValue

    from spoldzielnia_ai.audit.events import EventType
    from spoldzielnia_ai.auth.identity import OidcIdentity
    from spoldzielnia_ai.core.settings import Settings

SITE = "https://spoldzielnia.localhost"
HOST = "spoldzielnia.localhost"


@dataclass
class FakeUsers:
    """In-memory provisioner + directory, keyed by OIDC subject."""

    by_subject: dict[str, UserRecord] = field(default_factory=dict[str, UserRecord])

    async def provision(
        self, identity: OidcIdentity, provider: str, locale: str | None = None
    ) -> ProvisionedUser:
        existing = self.by_subject.get(identity.subject)
        record = UserRecord(
            id=existing.id if existing else uuid4(),
            roles=identity.roles,
            is_locked=existing.is_locked if existing else False,
            email=identity.email,
            display_name=identity.display_name,
        )
        self.by_subject[identity.subject] = record
        return ProvisionedUser(record.id, record.roles, record.is_locked, existing is None)

    async def get(self, user_id: UUID) -> UserRecord | None:
        return next((u for u in self.by_subject.values() if u.id == user_id), None)

    def lock(self, subject: str) -> None:
        user = self.by_subject[subject]
        self.by_subject[subject] = UserRecord(
            user.id, user.roles, True, user.email, user.display_name
        )


@dataclass
class AuditLog:
    events: list[tuple[EventType, UUID, Mapping[str, JsonValue]]] = field(
        default_factory=list[tuple["EventType", UUID, "Mapping[str, JsonValue]"]]
    )

    async def __call__(
        self, event: EventType, actor_id: UUID, payload: Mapping[str, JsonValue]
    ) -> None:
        self.events.append((event, actor_id, payload))


@pytest.fixture
def idp() -> Iterator[FakeIdP]:
    fake = FakeIdP()
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
def users() -> FakeUsers:
    return FakeUsers()


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def app(settings: Settings, redis: FakeAsyncRedis, users: FakeUsers, audit: AuditLog) -> FastAPI:
    app = create_app(settings, readiness_checks={})
    app.state.redis = redis
    app.state.audit_sink = audit
    app.dependency_overrides[get_user_provisioner] = lambda: users
    app.dependency_overrides[get_user_directory] = lambda: users

    expert = elevated_router(Role.EXPERT, prefix="/test/expert")
    admin = elevated_router(Role.ADMIN, prefix="/test/admin")

    @expert.get("")
    async def expert_endpoint(principal: CurrentUser) -> str:  # pyright: ignore[reportUnusedFunction]
        return str(principal.id)

    @admin.post("")
    async def admin_endpoint(principal: CurrentUser) -> str:  # pyright: ignore[reportUnusedFunction]
        return str(principal.id)

    app.include_router(expert)
    app.include_router(admin)
    return app


@pytest.fixture
async def client(app: FastAPI, idp: FakeIdP) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=SITE) as client:
        yield client


_subjects = itertools.count()


async def _login(
    client: AsyncClient,
    idp: FakeIdP,
    *,
    roles: list[str] | None = None,
    amr: list[str] | None = None,
    subject: str | None = None,
) -> Response:
    start = await client.get("/auth/login")
    params = parse_qs(urlsplit(start.headers["location"]).query)
    idp.id_token = idp.make_id_token(
        nonce=params["nonce"][0],
        sub=subject or f"user-{next(_subjects)}",
        roles=roles if roles is not None else ["MEMBER"],
        amr=amr if amr is not None else ["pwd"],
    )
    response = await client.get(
        "/auth/callback", params={"state": params["state"][0], "code": "the-code"}
    )
    assert response.status_code == 303, response.text
    return response


def _unsafe_headers(client: AsyncClient) -> dict[str, str]:
    return {"Origin": SITE, CSRF_HEADER: client.cookies.get(CSRF_COOKIE, domain=HOST) or ""}


def _sid(client: AsyncClient) -> str:
    sid = client.cookies.get(SID_COOKIE, domain=HOST)
    assert sid is not None
    return sid


async def _expire_access(redis: FakeAsyncRedis, sid: str) -> str:
    """Make the session due for a refresh. Returns its family id."""
    store = SessionStore(redis)
    session = await store.get_session(sid)
    assert session is not None
    family = await store.get_family(session.family_id)
    assert family is not None
    await store.save_family(
        session.family_id, family.model_copy(update={"access_expires_at": int(time.time()) - 1})
    )
    return session.family_id


# --- Session issuance ---------------------------------------------------------------------


def _set_cookies(response: Response) -> dict[str, str]:
    return {header.split("=", 1)[0]: header for header in response.headers.get_list("set-cookie")}


async def test_login_sets_hardened_session_cookies(client: AsyncClient, idp: FakeIdP) -> None:
    response = await _login(client, idp)
    cookies = _set_cookies(response)

    sid_cookie = cookies[SID_COOKIE].lower()
    for attribute in ("httponly", "secure", "samesite=strict", "path=/"):
        assert attribute in sid_cookie
    assert "domain=" not in sid_cookie
    assert "max-age" not in sid_cookie  # browser-session cookie; the server enforces lifetime

    csrf_cookie = cookies[CSRF_COOKIE].lower()
    assert "httponly" not in csrf_cookie  # the frontend must read it
    for attribute in ("secure", "samesite=strict", "path=/"):
        assert attribute in csrf_cookie

    assert len(_sid(client)) == 43  # 256-bit random


async def test_redis_holds_no_usable_secrets(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp)
    sid = _sid(client)
    raw_keys: list[bytes | str] = await redis.keys("*")  # pyright: ignore[reportUnknownMemberType]
    keys = [k.decode() if isinstance(k, bytes) else k for k in raw_keys]
    dump = b"".join([await redis.dump(k) or b"" for k in keys])  # every type, incl. sets
    assert f"session:{digest(sid)}" in keys
    assert not any(sid in k for k in keys)
    assert sid.encode() not in dump
    assert b"rt-login" not in dump  # refresh token sealed
    assert idp.id_token is not None
    assert idp.id_token.encode() not in dump  # ID token sealed


async def test_me_reports_profile_and_mfa(client: AsyncClient, idp: FakeIdP) -> None:
    assert (await client.get("/me")).status_code == 401
    await _login(client, idp, roles=["EXPERT", "MEMBER"], amr=["pwd", "otp"])
    me = (await client.get("/me")).json()
    assert me["roles"] == ["EXPERT", "MEMBER"]
    assert me["mfa_satisfied"] is True
    assert me["email"] == "pszczelarz@example.org"


async def test_relogin_drops_previous_session(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp, subject="same")
    first = _sid(client)
    await _login(client, idp, subject="same")
    second = _sid(client)
    assert first != second
    assert await SessionStore(redis).get_session(first) is None
    assert await SessionStore(redis).get_session(second) is not None


async def test_unknown_or_malformed_sid_is_401(client: AsyncClient) -> None:
    for sid in ("x" * 43, "short", "../../../etc"):
        client.cookies.set(SID_COOKIE, sid, domain=HOST)
        response = await client.get("/me")
        assert response.status_code == 401
        assert SID_COOKIE in response.headers.get("set-cookie", "")  # cleared


async def test_absolute_lifetime_ends_session(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp)
    sid = _sid(client)
    store = SessionStore(redis)
    session = await store.get_session(sid)
    assert session is not None
    expired = session.model_copy(update={"expires_at": int(time.time()) - 1})
    await redis.set(f"session:{digest(sid)}", expired.model_dump_json())
    assert (await client.get("/me")).status_code == 401
    assert await store.get_family(session.family_id) is None


async def test_locked_user_is_423_and_loses_session(
    client: AsyncClient, idp: FakeIdP, users: FakeUsers, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp, subject="soon-locked")
    sid = _sid(client)
    users.lock("soon-locked")
    assert (await client.get("/me")).status_code == 423
    assert await SessionStore(redis).get_session(sid) is None


# --- Refresh rotation ---------------------------------------------------------------------


async def test_expired_access_rotates_refresh_token(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp)
    await _expire_access(redis, _sid(client))

    assert (await client.get("/me")).status_code == 200
    assert idp.refreshed_with == ["rt-login-1"]
    store = SessionStore(redis)
    old = await store.get_refresh_record("rt-login-1")
    new = await store.get_refresh_record("rt-1")
    assert old is not None
    assert old.used
    assert new is not None
    assert not new.used

    assert (await client.get("/me")).status_code == 200
    assert idp.refreshed_with == ["rt-login-1"]  # fresh again: no second refresh


async def test_reused_refresh_token_revokes_family_and_is_audited(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis, audit: AuditLog
) -> None:
    from spoldzielnia_ai.audit.events import EventType

    await _login(client, idp, subject="victim")
    sid = _sid(client)
    family_id = await _expire_access(redis, sid)
    store = SessionStore(redis)
    stale = await store.get_family(family_id)  # snapshot holding rt-login-1
    assert (await client.get("/me")).status_code == 200  # rotates to rt-1

    # The rotated-out token comes back (e.g. a stolen copy of the old record).
    assert stale is not None
    await store.save_family(
        family_id, stale.model_copy(update={"access_expires_at": int(time.time()) - 1})
    )
    response = await client.get("/me")

    assert response.status_code == 401
    assert await store.get_family(family_id) is None
    assert await store.get_session(sid) is None
    assert idp.refreshed_with == ["rt-login-1"]  # the reused token never reached the IdP
    ((event, _actor, payload),) = audit.events
    assert event is EventType.REFRESH_TOKEN_REUSE
    assert payload["ip"] == "127.0.0.1"


async def test_concurrent_requests_refresh_once(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis, audit: AuditLog
) -> None:
    await _login(client, idp)
    await _expire_access(redis, _sid(client))
    idp.refresh_delay = 0.2

    responses = await asyncio.gather(*(client.get("/me") for _ in range(10)))

    assert [r.status_code for r in responses] == [200] * 10
    assert idp.refreshed_with == ["rt-login-1"]
    assert audit.events == []  # no false reuse alarm


async def test_idp_ended_session_logs_out(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp)
    family_id = await _expire_access(redis, _sid(client))
    idp.used_refresh_tokens.add("rt-login-1")  # IdP side: revoked / idle timeout
    assert (await client.get("/me")).status_code == 401
    assert await SessionStore(redis).get_family(family_id) is None


async def test_idp_outage_keeps_session(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    await _login(client, idp)
    await _expire_access(redis, _sid(client))
    idp.refresh_status = 503
    response = await client.get("/me")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"

    idp.refresh_status = 200
    assert (await client.get("/me")).status_code == 200


# --- Logout -------------------------------------------------------------------------------


async def test_logout_ends_session_everywhere(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis, audit: AuditLog
) -> None:
    from spoldzielnia_ai.audit.events import EventType

    await _login(client, idp)
    sid = _sid(client)
    response = await client.post("/auth/logout", headers=_unsafe_headers(client))

    assert response.status_code == 200
    url: str = response.json()["logout_url"]
    logout_url = urlsplit(url)
    assert f"{logout_url.scheme}://{logout_url.netloc}{logout_url.path}" == LOGOUT_URL
    query = parse_qs(logout_url.query)
    assert query["id_token_hint"] == [idp.id_token]
    assert query["post_logout_redirect_uri"] == [f"{SITE}/"]
    assert idp.revoked == ["rt-login-1"]
    assert await SessionStore(redis).get_session(sid) is None
    assert [e for e, _, _ in audit.events] == [EventType.USER_LOGOUT]
    cleared = _set_cookies(response)
    assert "max-age=0" in cleared[SID_COOKIE].lower()
    assert (await client.get("/me")).status_code == 401


# --- CSRF ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["no-token", "header-cookie-mismatch", "other-session-token", "cross-origin", "no-origin"],
)
async def test_unsafe_requests_need_csrf_proof(
    client: AsyncClient, idp: FakeIdP, app: FastAPI, case: str
) -> None:
    await _login(client, idp)
    headers = _unsafe_headers(client)
    match case:
        case "no-token":
            del headers[CSRF_HEADER]
        case "header-cookie-mismatch":
            headers[CSRF_HEADER] = "forged"
        case "other-session-token":
            forged = app.state.csrf_tokens.for_session("A" * 43)
            client.cookies.set(CSRF_COOKIE, forged, domain=HOST)
            headers[CSRF_HEADER] = forged
        case "cross-origin":
            headers["Origin"] = "https://evil.example"
        case "no-origin":
            del headers["Origin"]
        case _:
            raise AssertionError(case)
    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"  # security headers still on


async def test_same_origin_referer_is_accepted_without_origin(
    client: AsyncClient, idp: FakeIdP
) -> None:
    await _login(client, idp)
    headers = _unsafe_headers(client)
    del headers["Origin"]
    headers["Referer"] = f"{SITE}/settings"
    assert (await client.post("/auth/logout", headers=headers)).status_code == 200


# --- RBAC x MFA matrix --------------------------------------------------------------------

ROLE_CLAIMS = {"MEMBER": ["MEMBER"], "EXPERT": ["MEMBER", "EXPERT"], "ADMIN": ["MEMBER", "ADMIN"]}


@pytest.mark.parametrize("mfa", [True, False], ids=["mfa", "no-mfa"])
@pytest.mark.parametrize("role", ["MEMBER", "EXPERT", "ADMIN"])
async def test_role_mfa_matrix(client: AsyncClient, idp: FakeIdP, role: str, mfa: bool) -> None:
    await _login(client, idp, roles=ROLE_CLAIMS[role], amr=["pwd", "otp"] if mfa else ["pwd"])

    expert = await client.get("/test/expert")
    admin = await client.post("/test/admin", headers=_unsafe_headers(client))
    me = await client.get("/me")

    def expected(required: str) -> int:
        return 200 if role == required and mfa else 403

    assert (expert.status_code, admin.status_code) == (expected("EXPERT"), expected("ADMIN"))
    assert me.status_code == 200
    assert me.json()["mfa_satisfied"] is mfa

    for response, required in ((expert, "EXPERT"), (admin, "ADMIN")):
        if role == required and not mfa:
            detail: dict[str, Any] = response.json()["detail"]
            assert detail["error"] == "mfa_required"
            assert detail["step_up"] == "/api/auth/login?prompt=login&acr_values=mfa"
            challenge = response.headers["www-authenticate"]
            assert 'error="insufficient_user_authentication"' in challenge


async def test_mfa_via_acr_is_accepted(client: AsyncClient, idp: FakeIdP) -> None:
    start = await client.get("/auth/login")
    params = parse_qs(urlsplit(start.headers["location"]).query)
    idp.id_token = idp.make_id_token(
        nonce=params["nonce"][0], roles=["EXPERT"], amr=["pwd"], acr="mfa"
    )
    await client.get("/auth/callback", params={"state": params["state"][0], "code": "c"})
    assert (await client.get("/test/expert")).status_code == 200


def test_mfa_switched_off_satisfies_every_session(settings: Settings) -> None:
    session = SessionData(
        user_id=uuid4(),
        family_id="f",
        acr=None,
        amr=["pwd"],
        auth_time=None,
        created_at=0,
        expires_at=1,
    )
    assert is_mfa_satisfied(session, settings.security) is False
    relaxed = settings.security.model_copy(update={"mfa_required": False})
    assert is_mfa_satisfied(session, relaxed) is True


def test_elevated_router_refuses_member_role() -> None:
    with pytest.raises(ValueError, match="elevated_router"):
        elevated_router(Role.MEMBER)


# --- Per-user revocation (8.2) ----------------------------------------------------------------


async def test_revoke_user_ends_every_family_of_that_user_only(redis: FakeAsyncRedis) -> None:
    from spoldzielnia_ai.auth.sessions import FamilyData

    store = SessionStore(redis)
    alice, bob = uuid4(), uuid4()
    now = int(time.time())

    async def open_session(user: UUID, family_id: str, lifetime: int) -> str:
        sid = f"{family_id}-sid".ljust(43, "x")
        session = SessionData(
            user_id=user,
            family_id=family_id,
            acr=None,
            amr=["pwd"],
            auth_time=now,
            created_at=now,
            expires_at=now + lifetime,
        )
        family = FamilyData(
            user_id=user,
            refresh_token=None,
            id_token=None,
            access_expires_at=now + 60,
            expires_at=now + lifetime,
        )
        await store.create(sid, session, family, f"rt-{family_id}")
        return sid

    laptop = await open_session(alice, "fam-a1", 3600)
    phone = await open_session(alice, "fam-a2", 7200)
    other = await open_session(bob, "fam-b1", 3600)
    # The index lives as long as the longest family.
    assert await redis.expiretime(f"user:{alice}:families") == now + 7200

    assert await store.delete_user_families(alice) == 2

    assert await store.get_session(laptop) is None
    assert await store.get_session(phone) is None
    assert await store.get_family("fam-a1") is None
    assert await store.get_session(other) is not None
    assert not await redis.exists(f"user:{alice}:families")
    assert await store.delete_user_families(alice) == 0


async def test_a_revoked_user_is_signed_out_on_the_next_request(
    client: AsyncClient, idp: FakeIdP, app: FastAPI
) -> None:
    await _login(client, idp, subject="revoked-user")
    me = await client.get("/me")
    assert me.status_code == 200
    user_id = UUID(me.json()["id"])

    manager = SessionManager(
        SessionStore(app.state.redis),
        app.state.oidc_client,
        app.state.token_sealer,
        app.state.settings.security,
        app.state.audit_sink,
    )
    assert await manager.revoke_user(user_id) == 1
    assert (await client.get("/me")).status_code == 401
