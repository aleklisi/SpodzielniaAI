"""Chunk 8.1: the Keycloak Admin REST client (service-account token, error mapping, no body
logging). The real realm is exercised in ``tests/integration/test_keycloak_admin.py``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog
from pydantic import SecretStr
from structlog.testing import capture_logs

from spoldzielnia_ai.auth import keycloak_admin
from spoldzielnia_ai.auth.keycloak_admin import (
    ActionEmailFailed,
    IdpUnavailable,
    KeycloakAdmin,
    KeycloakAdminError,
    PasswordRejected,
    UserExists,
    UserNotFound,
)
from spoldzielnia_ai.core.logging import configure_logging
from spoldzielnia_ai.core.settings import KeycloakAdminSettings

if TYPE_CHECKING:
    from collections.abc import Callable

BASE = "http://kc.test"
API = f"{BASE}/admin/realms/spoldzielnia"
TOKEN_URL = f"{BASE}/realms/spoldzielnia/protocol/openid-connect/token"


class FakeKeycloak:
    """Records requests; ``routes`` maps (method, path) to a response factory."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens_issued = 0
        self.expires_in = 300
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            self.tokens_issued += 1
            token = f"t{self.tokens_issued}"
            return httpx.Response(200, json={"access_token": token, "expires_in": self.expires_in})
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404)
        return route(request)

    def api(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) != TOKEN_URL]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def kc() -> FakeKeycloak:
    return FakeKeycloak()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def _admin(kc: FakeKeycloak, clock: Clock, secret: str | None = "s3cret") -> KeycloakAdmin:  # noqa: S107
    settings = KeycloakAdminSettings(
        base_url=BASE, client_secret=SecretStr(secret) if secret else None
    )
    return KeycloakAdmin(settings, clock=clock, transport=httpx.MockTransport(kc))


USER = {
    "id": "kc-1",
    "username": "ola",
    "email": "ola@example.test",
    "firstName": "Ola",
    "lastName": "Nowak",
    "enabled": True,
    "emailVerified": True,
    "requiredActions": ["UPDATE_PASSWORD"],
    "totp": True,
    "createdTimestamp": 1,
}


async def test_token_is_cached_and_renewed_before_expiry(kc: FakeKeycloak, clock: Clock) -> None:
    kc.routes[("GET", "/admin/realms/spoldzielnia/users")] = lambda r: httpx.Response(
        200, json=[USER]
    )
    admin = _admin(kc, clock)

    await admin.search_users()
    await admin.search_users()
    assert kc.tokens_issued == 1
    token_request = kc.requests[0]
    form = dict(httpx.QueryParams(token_request.content.decode()))
    assert form == {
        "grant_type": "client_credentials",
        "client_id": "spoldzielnia-admin",
        "client_secret": "s3cret",
    }
    assert all(r.headers["authorization"] == "Bearer t1" for r in kc.api())

    clock.now += 300 - 30  # inside the safety margin
    await admin.search_users()
    assert kc.tokens_issued == 2


async def test_a_401_renews_the_token_once(kc: FakeKeycloak, clock: Clock) -> None:
    def users(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json=[])

    kc.routes[("GET", "/admin/realms/spoldzielnia/users")] = users
    assert await _admin(kc, clock).search_users() == []
    assert kc.tokens_issued == 2


async def test_search_is_infix_and_parses_users(kc: FakeKeycloak, clock: Clock) -> None:
    kc.routes[("GET", "/admin/realms/spoldzielnia/users")] = lambda r: httpx.Response(
        200, json=[USER]
    )
    [user] = await _admin(kc, clock).search_users("ola", first=10, maximum=5)

    params = kc.api()[0].url.params
    assert (params["search"], params["first"], params["max"]) == ("*ola*", "10", "5")
    assert params["briefRepresentation"] == "false"
    assert user.id == "kc-1"
    assert user.display_name == "Ola Nowak"
    assert user.required_actions == ("UPDATE_PASSWORD",)
    assert user.totp is True


async def test_get_user_includes_brokered_identities(kc: FakeKeycloak, clock: Clock) -> None:
    path = "/admin/realms/spoldzielnia/users/kc-1"
    kc.routes[("GET", path)] = lambda r: httpx.Response(200, json=USER)
    kc.routes[("GET", f"{path}/federated-identity")] = lambda r: httpx.Response(
        200, json=[{"identityProvider": "google", "userId": "g1", "userName": "ola"}]
    )
    user = await _admin(kc, clock).get_user("kc-1")
    assert user.identity_providers == ("google",)


async def test_create_returns_the_id_from_location(kc: FakeKeycloak, clock: Clock) -> None:
    kc.routes[("POST", "/admin/realms/spoldzielnia/users")] = lambda r: httpx.Response(
        201, headers={"Location": f"{API}/users/new-id"}
    )
    user_id = await _admin(kc, clock).create_user(
        username="ola",
        email=None,
        first_name="Ola",
        last_name=None,
        required_actions=["UPDATE_PASSWORD"],
    )
    assert user_id == "new-id"
    body = json.loads(kc.api()[0].content)
    assert body == {
        "username": "ola",
        "enabled": True,
        "emailVerified": False,
        "requiredActions": ["UPDATE_PASSWORD"],
        "firstName": "Ola",
    }


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, UserNotFound), (409, UserExists), (500, IdpUnavailable), (403, KeycloakAdminError)],
)
async def test_errors_are_mapped(
    kc: FakeKeycloak, clock: Clock, status: int, error: type[Exception]
) -> None:
    kc.routes[("POST", "/admin/realms/spoldzielnia/users/kc-1/logout")] = lambda r: httpx.Response(
        status
    )
    with pytest.raises(error):
        await _admin(kc, clock).logout("kc-1")


async def test_password_policy_rejection_carries_the_reason(kc: FakeKeycloak, clock: Clock) -> None:
    kc.routes[("PUT", "/admin/realms/spoldzielnia/users/kc-1/reset-password")] = lambda r: (
        httpx.Response(
            400,
            json={
                "error": "invalidPasswordMinLengthMessage",
                "error_description": "Invalid password: minimum length 12.",
            },
        )
    )
    with pytest.raises(PasswordRejected) as raised:
        await _admin(kc, clock).set_temporary_password("kc-1", "short")
    assert raised.value.reason == "invalidPasswordMinLengthMessage"


async def test_action_email_failure_is_reported(kc: FakeKeycloak, clock: Clock) -> None:
    path = "/admin/realms/spoldzielnia/users/kc-1/execute-actions-email"
    kc.routes[("PUT", path)] = lambda r: httpx.Response(500)
    with pytest.raises(ActionEmailFailed):
        await _admin(kc, clock).send_actions_email("kc-1", ["UPDATE_PASSWORD"])


async def test_roles_are_resolved_from_the_users_mappings(kc: FakeKeycloak, clock: Clock) -> None:
    base = "/admin/realms/spoldzielnia/users/kc-1/role-mappings/realm"
    kc.routes[("GET", f"{base}/available")] = lambda r: httpx.Response(
        200, json=[{"id": "r-exp", "name": "EXPERT"}, {"id": "r-adm", "name": "ADMIN"}]
    )
    kc.routes[("POST", base)] = lambda r: httpx.Response(204)
    admin = _admin(kc, clock)

    await admin.add_realm_roles("kc-1", ["EXPERT"])
    assert json.loads(kc.api()[-1].content) == [{"id": "r-exp", "name": "EXPERT"}]
    with pytest.raises(KeycloakAdminError, match="not available"):
        await admin.add_realm_roles("kc-1", ["realm-admin"])


async def test_unconfigured_or_unreachable_keycloak_is_unavailable(
    kc: FakeKeycloak, clock: Clock
) -> None:
    unconfigured = _admin(kc, clock, secret=None)
    assert unconfigured.configured is False
    with pytest.raises(IdpUnavailable):
        await unconfigured.search_users()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = KeycloakAdminSettings(base_url=BASE, client_secret=SecretStr("s"))
    with pytest.raises(IdpUnavailable):
        await KeycloakAdmin(settings, transport=httpx.MockTransport(down)).search_users()


async def test_passwords_never_reach_the_logs(
    kc: FakeKeycloak, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "/admin/realms/spoldzielnia/users/kc-1/reset-password"
    kc.routes[("PUT", path)] = lambda r: httpx.Response(204)
    # Loggers are cached at first use: a fresh one at DEBUG, whatever ran before.
    configure_logging("DEBUG", json=False)
    monkeypatch.setattr(keycloak_admin, "log", structlog.get_logger(keycloak_admin.__name__))
    with capture_logs() as logs:
        await _admin(kc, clock).set_temporary_password("kc-1", "Very-Secret-Temp-42")
    assert logs
    assert "Very-Secret-Temp-42" not in repr(logs)
    assert "s3cret" not in repr(logs)
