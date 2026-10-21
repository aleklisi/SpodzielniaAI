"""Chunk 8.4: the ``/admin/users`` API (ADMIN + MFA) with the real app, Postgres and an
in-memory Keycloak. Rules themselves are covered in ``test_user_admin.py``; this checks the
HTTP contract, authorization, step-up, limits and that sessions really end."""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.auth.keycloak_admin import IdpUnavailable
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import harness
from tests.fixtures.keycloak import FakeUserIdp

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

MFA: dict[str, Any] = {"amr": ["pwd", "otp"]}


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def kc() -> FakeUserIdp:
    return FakeUserIdp()


def _settings(settings: Settings, **kc: Any) -> Settings:
    return settings.model_copy(
        update={"keycloak_admin": settings.keycloak_admin.model_copy(update=kc)}
    )


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory, kc: FakeUserIdp) -> AsyncIterator[Harness]:
    async with harness(_settings(settings, temporary_passwords_per_hour=3), factory) as h:
        h.app.state.user_idp = kc
        yield h


async def _login(
    h: Harness, kc: FakeUserIdp, username: str, *roles: str, **claims: Any
) -> LoggedIn:
    """A user known to Keycloak (same subject) and logged in to the app."""
    user = kc.add(username, "MEMBER", *roles, totp="ADMIN" in roles or "EXPERT" in roles)
    return await h.login(
        subject=user.id,
        roles=["MEMBER", *roles],
        preferred_username=username,
        **claims,
    )


async def _admin(h: Harness, kc: FakeUserIdp, **claims: Any) -> LoggedIn:
    return await _login(h, kc, "admin1", "ADMIN", **{**MFA, **claims})


async def test_authorization_matrix(h: Harness, kc: FakeUserIdp) -> None:
    member = await _login(h, kc, "member1")
    expert = await _login(h, kc, "expert1", "EXPERT", **MFA)
    admin_without_mfa = await _login(h, kc, "admin2", "ADMIN")
    admin = await _admin(h, kc)
    body = {"username": "nowa", "password_mode": "temporary"}
    for user in (member, expert, admin_without_mfa):
        assert (await user.client.get("/admin/users")).status_code == 403
        response = await user.client.post("/admin/users", json=body, headers=user.headers)
        assert response.status_code == 403
        path = f"/admin/users/{admin.user_id}/block"
        blocked = await user.client.post(path, json={"reason": "other"}, headers=user.headers)
        assert blocked.status_code == 403
    assert (await admin.client.get("/admin/users")).status_code == 200
    # CSRF still applies to the admin.
    no_csrf = await admin.client.post("/admin/users", json=body)
    assert no_csrf.status_code == 403


async def test_create_list_and_detail(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    response = await admin.client.post(
        "/admin/users",
        json={
            "username": "Nowa.Osoba",
            "email": "nowa@example.test",
            "first_name": "Nowa",
            "last_name": "Osoba",
            "roles": ["MEMBER"],
            "password_mode": "temporary",
        },
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    password = created["temporary_password"]
    assert len(password) == 16
    user = created["user"]
    assert (user["username"], user["status"], user["roles"]) == (
        "nowa.osoba",
        "pending_setup",
        ["MEMBER"],
    )

    page = (await admin.client.get("/admin/users", params={"q": "nowa"})).json()
    assert page["total"] == 1
    assert [u["username"] for u in page["items"]] == ["nowa.osoba"]
    assert password not in str(page)

    detail = (await admin.client.get(f"/admin/users/{user['id']}")).json()
    assert detail["idp"]["required_actions"] == ["UPDATE_PASSWORD"]
    assert detail["idp"]["enabled"] is True
    assert detail["created_by"] == admin.user_id
    me = (await admin.client.get(f"/admin/users/{admin.user_id}")).json()
    assert me["is_self"] is True
    assert me["other_active_admins"] == 0  # the UI explains why demoting is disabled


@pytest.mark.parametrize(
    "body",
    [
        {"username": "ok-name", "roles": ["SUPERUSER"]},
        {"username": "ok-name", "roles": ["realm-admin"]},
        {"username": "ok-name", "email": "not-an-address"},
        {"username": "x"},
        {"username": "ok-name", "password_mode": "plain"},
    ],
)
async def test_invalid_input_is_rejected(h: Harness, kc: FakeUserIdp, body: dict[str, Any]) -> None:
    admin = await _admin(h, kc)
    response = await admin.client.post("/admin/users", json=body, headers=admin.headers)
    assert response.status_code == 422
    assert all(u.username != "ok-name" for u in kc.users.values())


async def test_privilege_grants_need_a_fresh_login(h: Harness, kc: FakeUserIdp) -> None:
    stale = await _admin(h, kc, auth_time=int(time.time()) - 3600)
    member = await _login(h, kc, "member1")
    response = await stale.client.put(
        f"/admin/users/{member.user_id}/roles", json={"roles": ["EXPERT"]}, headers=stale.headers
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error"] == "reauthentication_required"
    assert detail["step_up"].startswith("/api/auth/login?prompt=login")
    # Removing or keeping roles, blocking: no step-up needed.
    blocked = await stale.client.post(
        f"/admin/users/{member.user_id}/block",
        json={"reason": "other"},
        headers=stale.headers,
    )
    assert blocked.status_code == 200


async def test_block_ends_open_sessions_at_once(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    member = await _login(h, kc, "member1")
    second_device = await h.login(subject=kc.users[_kc_id(kc, "member1")].id, roles=["MEMBER"])
    assert (await member.client.get("/me")).status_code == 200

    response = await admin.client.post(
        f"/admin/users/{member.user_id}/block",
        json={"reason": "left_cooperative", "note": "Rezygnacja"},
        headers=admin.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert (await member.client.get("/me")).status_code == 401  # session gone
    assert (await second_device.client.get("/me")).status_code == 401
    assert kc.users[_kc_id(kc, "member1")].enabled is False

    # A fresh login through the app is refused while blocked.
    with pytest.raises(AssertionError, match="Account locked"):
        await h.login(subject=_kc_id(kc, "member1"), roles=["MEMBER"])

    unblocked = await admin.client.post(
        f"/admin/users/{member.user_id}/unblock", headers=admin.headers
    )
    assert unblocked.json()["status"] == "active"


def _kc_id(kc: FakeUserIdp, username: str) -> str:
    return next(u.id for u in kc.users.values() if u.username == username)


async def test_password_actions_and_sign_out(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    member = await _login(h, kc, "member1")
    base = f"/admin/users/{member.user_id}"

    forced = await admin.client.post(
        f"{base}/require-password-change", json={"sign_out": False}, headers=admin.headers
    )
    assert forced.json()["required_actions"] == ["UPDATE_PASSWORD"]
    assert (await member.client.get("/me")).status_code == 200  # still signed in

    reset = await admin.client.post(
        f"{base}/password-reset", json={"mode": "temporary"}, headers=admin.headers
    )
    assert reset.status_code == 200
    assert len(reset.json()["temporary_password"]) == 16
    assert (await member.client.get("/me")).status_code == 401  # a reset signs out

    member = await h.login(subject=_kc_id(kc, "member1"), roles=["MEMBER"])
    out = await admin.client.post(f"{base}/sign-out", headers=admin.headers)
    assert out.json() == {"app_sessions": 1}
    assert (await member.client.get("/me")).status_code == 401

    kc.users[_kc_id(kc, "member1")] = dataclasses.replace(
        kc.users[_kc_id(kc, "member1")], identity_providers=("google",)
    )
    external = await admin.client.post(
        f"{base}/password-reset", json={"mode": "email"}, headers=admin.headers
    )
    assert (external.status_code, external.json()["detail"]["error"]) == (
        409,
        "external_identity",
    )


async def test_temporary_passwords_are_rate_limited(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    member = await _login(h, kc, "member1")
    path = f"/admin/users/{member.user_id}/password-reset"
    codes = [
        (
            await admin.client.post(path, json={"mode": "temporary"}, headers=admin.headers)
        ).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    # E-mail resets are not limited by the temporary-password budget.
    email = await admin.client.post(path, json={"mode": "email"}, headers=admin.headers)
    assert email.status_code == 200


async def test_guardrails_answer_409(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    for path, body in (
        (f"/admin/users/{admin.user_id}/block", {"reason": "other"}),
        (f"/admin/users/{admin.user_id}/roles", {"roles": ["MEMBER"]}),
    ):
        method = admin.client.put if path.endswith("roles") else admin.client.post
        response = await method(path, json=body, headers=admin.headers)
        assert response.status_code == 409
        assert response.json()["detail"]["error"] in {"cannot_block_self", "cannot_demote_self"}
    unknown = await admin.client.get("/admin/users/00000000-0000-7000-8000-000000000000")
    assert unknown.status_code == 404


async def test_keycloak_down_or_unconfigured(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    member = await _login(h, kc, "member1")
    kc.fail_on, kc.fail_with = "logout", IdpUnavailable
    response = await admin.client.post(
        f"/admin/users/{member.user_id}/sign-out", headers=admin.headers
    )
    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "idp_unavailable"}
    assert (await member.client.get("/me")).status_code == 200  # nothing half-done

    kc.fail_on = None
    kc.configured = False
    response = await admin.client.post(
        f"/admin/users/{member.user_id}/sign-out", headers=admin.headers
    )
    assert (response.status_code, response.json()["detail"]["error"]) == (
        503,
        "user_admin_unavailable",
    )
    # The list still works from the mirror.
    assert (await admin.client.get("/admin/users")).status_code == 200


async def test_sync_imports_console_users(h: Harness, kc: FakeUserIdp) -> None:
    admin = await _admin(h, kc)
    kc.add("z-konsoli", "MEMBER")
    response = await admin.client.post("/admin/users/sync", headers=admin.headers)
    assert response.json() == {"seen": 2, "created": 1, "updated": 0, "blocked": 0}
    page = (await admin.client.get("/admin/users", params={"q": "konsoli"})).json()
    assert page["items"][0]["status"] == "active"
