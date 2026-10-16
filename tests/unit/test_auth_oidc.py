"""Chunk 1.5: OIDC Authorization Code + PKCE through the real app, against a fake IdP."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
import respx
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from joserfc.jwk import OctKey, RSAKey

from spoldzielnia_ai.auth.deps import get_user_provisioner
from spoldzielnia_ai.auth.login_state import KEY_PREFIX
from spoldzielnia_ai.auth.oidc_client import OidcClient, OidcTokenError
from spoldzielnia_ai.auth.pkce import code_challenge_s256
from spoldzielnia_ai.auth.provisioning import ProvisionedUser
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.main import create_app
from tests.fixtures.oidc import AUTHORIZE_URL, CLIENT_ID, CLIENT_SECRET, ISSUER, FakeIdP

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI

    from spoldzielnia_ai.auth.identity import OidcIdentity
    from spoldzielnia_ai.core.settings import Settings

BASE_URL = "https://spoldzielnia.localhost"
LOGIN_COOKIE = "__Host-oidc_login"


@dataclass
class FakeProvisioner:
    locked: bool = False
    calls: list[tuple[OidcIdentity, str]] = field(default_factory=list[tuple["OidcIdentity", str]])

    async def provision(self, identity: OidcIdentity, provider: str) -> ProvisionedUser:
        self.calls.append((identity, provider))
        return ProvisionedUser(
            id=UUID(int=1), roles=identity.roles, is_locked=self.locked, created=True
        )


@pytest.fixture
def idp() -> Iterator[FakeIdP]:
    fake = FakeIdP()
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
def provisioner() -> FakeProvisioner:
    return FakeProvisioner()


@pytest.fixture
def redis() -> FakeAsyncRedis:
    return FakeAsyncRedis()


@pytest.fixture
def app(settings: Settings, redis: FakeAsyncRedis, provisioner: FakeProvisioner) -> FastAPI:
    app = create_app(settings, readiness_checks={})
    app.state.redis = redis
    app.dependency_overrides[get_user_provisioner] = lambda: provisioner
    return app


@pytest.fixture
async def client(app: FastAPI, idp: FakeIdP) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as client:
        yield client


async def _start_login(client: AsyncClient, **params: str) -> dict[str, str]:
    response = await client.get("/auth/login", params=params)
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    return {k: v[0] for k, v in parse_qs(location.query).items()}


async def _callback(client: AsyncClient, state: str, **params: str) -> Any:
    return await client.get("/auth/callback", params={"state": state, "code": "the-code", **params})


async def _login(client: AsyncClient, idp: FakeIdP, *, return_to: str = "/", **claims: Any) -> Any:
    auth = await _start_login(client, return_to=return_to)
    idp.id_token = idp.make_id_token(nonce=auth["nonce"], **claims)
    return await _callback(client, auth["state"])


# --- /auth/login ------------------------------------------------------------------------


async def test_login_redirects_to_idp_with_pkce(client: AsyncClient, redis: FakeAsyncRedis) -> None:
    response = await client.get("/auth/login")
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == AUTHORIZE_URL
    params = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == "https://spoldzielnia.localhost/api/auth/callback"
    assert params["scope"].split() == ["openid", "profile", "email"]
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) == 43
    assert len(params["nonce"]) == 43
    assert "acr_values" not in params
    assert "prompt" not in params

    stored = await redis.get(KEY_PREFIX + params["state"])
    assert isinstance(stored, bytes)
    assert 0 < await redis.ttl(KEY_PREFIX + params["state"]) <= 600
    assert b"code_verifier" in stored
    assert params["code_challenge"] not in stored.decode()  # only the verifier is kept

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{LOGIN_COOKIE}={params['state']};")
    lowered = cookie.lower()
    for attribute in ("httponly", "secure", "samesite=lax", "path=/", "max-age=600"):
        assert attribute in lowered
    assert "domain=" not in lowered  # __Host- prefix forbids Domain


async def test_login_forwards_step_up_parameters(client: AsyncClient) -> None:
    params = await _start_login(client, acr_values="mfa", prompt="login")
    assert params["acr_values"] == "mfa"
    assert params["prompt"] == "login"


@pytest.mark.parametrize(
    "params",
    [{"provider": "google"}, {"acr_values": "gold"}, {"prompt": "none"}],
    ids=["provider", "acr_values", "prompt"],
)
async def test_login_rejects_unsupported_parameters(
    client: AsyncClient, params: dict[str, str]
) -> None:
    assert (await client.get("/auth/login", params=params)).status_code == 400


async def test_login_reports_unreachable_idp(client: AsyncClient, idp: FakeIdP) -> None:
    idp.discovery["issuer"] = "https://evil.example/realms/spoldzielnia"
    assert (await client.get("/auth/login")).status_code == 502


# --- /auth/callback ---------------------------------------------------------------------


async def test_callback_exchanges_code_and_provisions_user(
    client: AsyncClient, idp: FakeIdP, provisioner: FakeProvisioner
) -> None:
    auth = await _start_login(client, return_to="/wiki/warroza?tab=2")
    idp.id_token = idp.make_id_token(nonce=auth["nonce"])
    response = await _callback(client, auth["state"])

    assert response.status_code == 303
    assert response.headers["location"] == "/wiki/warroza?tab=2"
    assert f'{LOGIN_COOKIE}=""' in response.headers["set-cookie"]

    (request,) = idp.token_requests
    form = FakeIdP.form(request)
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == "https://spoldzielnia.localhost/api/auth/callback"
    assert code_challenge_s256(form["code_verifier"]) == auth["code_challenge"]
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {basic}"

    ((identity, provider),) = provisioner.calls
    assert provider == "keycloak"
    assert (identity.issuer, identity.subject) == (ISSUER, "kc-sub-1")
    assert identity.roles == (Role.MEMBER,)
    assert identity.email == "pszczelarz@example.org"
    assert identity.display_name == "Jan Pszczelarz"
    assert identity.amr == ("pwd",)


async def test_es256_id_token_accepted(client: AsyncClient, idp: FakeIdP) -> None:
    auth = await _start_login(client)
    idp.id_token = idp.make_id_token(key=idp.ec, nonce=auth["nonce"])
    assert (await _callback(client, auth["state"])).status_code == 303


async def test_replayed_state_is_rejected(client: AsyncClient, idp: FakeIdP) -> None:
    auth = await _start_login(client)
    idp.id_token = idp.make_id_token(nonce=auth["nonce"])
    assert (await _callback(client, auth["state"])).status_code == 303
    client.cookies.set(LOGIN_COOKIE, auth["state"], domain="spoldzielnia.localhost")
    replay = await _callback(client, auth["state"])
    assert replay.status_code == 400
    assert len(idp.token_requests) == 1


async def test_callback_requires_the_browser_that_started_login(
    client: AsyncClient, idp: FakeIdP
) -> None:
    """Login CSRF: a victim following an attacker's callback URL has no matching cookie."""
    auth = await _start_login(client)
    client.cookies.clear()
    assert (await _callback(client, auth["state"])).status_code == 400

    other = await _start_login(client)  # the victim's own, different login in progress
    assert (await _callback(client, auth["state"])).status_code == 400
    assert other["state"] != auth["state"]
    assert idp.token_requests == []


@pytest.mark.parametrize("state", ["", "short", "x" * 43, "../../etc/passwd" + "a" * 27])
async def test_unknown_state_is_rejected(client: AsyncClient, state: str) -> None:
    client.cookies.set(LOGIN_COOKIE, state, domain="spoldzielnia.localhost")
    assert (await _callback(client, state)).status_code == 400


async def test_provider_error_consumes_state(
    client: AsyncClient, idp: FakeIdP, redis: FakeAsyncRedis
) -> None:
    auth = await _start_login(client)
    response = await client.get(
        "/auth/callback", params={"state": auth["state"], "error": "access_denied"}
    )
    assert response.status_code == 400
    assert await redis.exists(KEY_PREFIX + auth["state"]) == 0
    assert idp.token_requests == []


async def test_missing_code_is_rejected(client: AsyncClient) -> None:
    auth = await _start_login(client)
    response = await client.get("/auth/callback", params={"state": auth["state"]})
    assert response.status_code == 400


async def test_rejected_code_is_401(client: AsyncClient, idp: FakeIdP) -> None:
    idp.token_status, idp.token_body = 400, {"error": "invalid_grant"}
    auth = await _start_login(client)
    assert (await _callback(client, auth["state"])).status_code == 401


async def test_idp_failure_during_exchange_is_502(client: AsyncClient, idp: FakeIdP) -> None:
    idp.token_status, idp.token_body = 503, {"error": "temporarily_unavailable"}
    auth = await _start_login(client)
    assert (await _callback(client, auth["state"])).status_code == 502


async def test_locked_user_is_refused(
    client: AsyncClient, idp: FakeIdP, provisioner: FakeProvisioner
) -> None:
    provisioner.locked = True
    assert (await _login(client, idp)).status_code == 423


@pytest.mark.parametrize(
    "return_to",
    [
        "https://evil.example/",
        "//evil.example/x",
        "/\\evil.example",
        "http://spoldzielnia.localhost/x",
        "javascript:alert(1)",
        "wiki",
        "/ok\r\nSet-Cookie: x=1",
    ],
)
async def test_tampered_return_to_falls_back_to_root(
    client: AsyncClient, idp: FakeIdP, return_to: str
) -> None:
    response = await _login(client, idp, return_to=return_to)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_allowed_absolute_return_to_is_reduced_to_path(
    client: AsyncClient, idp: FakeIdP
) -> None:
    response = await _login(client, idp, return_to="https://spoldzielnia.localhost/me?x=1")
    assert response.headers["location"] == "/me?x=1"


# --- ID token validation ----------------------------------------------------------------

_NOW = int(time.time())


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "someone-else"},
        {"aud": [CLIENT_ID, "someone-else"], "azp": "someone-else"},
        {"aud": [CLIENT_ID, "someone-else"], "azp": None},
        {"iss": "https://evil.example/realms/spoldzielnia"},
        {"nonce": "another-nonce"},
        {"exp": _NOW - 120},
        {"iat": _NOW + 600},
        {"sub": None},
        {"sub": ""},
    ],
    ids=[
        "aud",
        "azp-mismatch",
        "azp-missing",
        "iss",
        "nonce",
        "expired",
        "iat-future",
        "sub-missing",
        "sub-empty",
    ],
)
async def test_invalid_id_token_claims_are_401(
    client: AsyncClient, idp: FakeIdP, claims: dict[str, Any]
) -> None:
    auth = await _start_login(client)
    claims.setdefault("nonce", auth["nonce"])
    idp.id_token = idp.make_id_token(**claims)
    assert (await _callback(client, auth["state"])).status_code == 401


async def test_leeway_tolerates_small_clock_skew(client: AsyncClient, idp: FakeIdP) -> None:
    assert (await _login(client, idp, exp=int(time.time()) - 30)).status_code == 303


def _bad_token(idp: FakeIdP, kind: str, nonce: str) -> str:
    from tests.fixtures.oidc import unsigned_token

    claims = idp.claims(nonce=nonce)
    match kind:
        case "alg-none":
            return unsigned_token({"alg": "none", "kid": "rsa-1"}, claims)
        case "hs256-client-secret":
            secret = OctKey.import_key(CLIENT_SECRET.encode())
            return idp.make_id_token(key=secret, alg="HS256", header={"kid": "rsa-1"}, nonce=nonce)
        case "unknown-kid":
            rogue = RSAKey.generate_key(2048, parameters={"kid": "rogue"})
            return idp.make_id_token(key=rogue, nonce=nonce)
        case "forged-known-kid":
            rogue = RSAKey.generate_key(2048, parameters={"kid": "rsa-1"})
            return idp.make_id_token(key=rogue, nonce=nonce)
        case "encryption-key":
            # Signed with the private half of the published `use: enc` key (kid enc-1).
            return idp.make_id_token(
                key=idp.signing_copy(idp.enc, use="sig", alg="RS256"), nonce=nonce
            )
        case "no-kid":
            without_kid = {k: v for k, v in idp.rsa.as_dict(private=True).items() if k != "kid"}
            return idp.make_id_token(key=RSAKey.import_key(without_kid), nonce=nonce)
        case "malformed":
            return "not-a-jwt"
        case _:
            raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "alg-none",
        "hs256-client-secret",
        "unknown-kid",
        "forged-known-kid",
        "encryption-key",
        "no-kid",
        "malformed",
    ],
)
@pytest.mark.filterwarnings("ignore::joserfc.errors.SecurityWarning")  # the HS256 attacker key
async def test_untrusted_signatures_are_401(client: AsyncClient, idp: FakeIdP, kind: str) -> None:
    auth = await _start_login(client)
    idp.id_token = _bad_token(idp, kind, auth["nonce"])
    assert (await _callback(client, auth["state"])).status_code == 401


# --- Key rotation (OidcClient directly) -------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_unknown_kid_refreshes_jwks_rate_limited(settings: Settings, idp: FakeIdP) -> None:
    clock = Clock()
    oidc = OidcClient(settings.oidc, clock=clock)
    try:
        await oidc.validate_id_token(idp.make_id_token(nonce="n"), "n")
        assert idp.jwks_calls == 1

        rotated = RSAKey.generate_key(2048, parameters={"kid": "rsa-2", "use": "sig"})
        idp.published = [rotated, idp.rsa]
        new_token = idp.make_id_token(key=rotated, nonce="n")

        # Within the refresh interval a kid miss does not reach the IdP.
        clock.now += settings.oidc.jwks_min_refresh_seconds - 1
        with pytest.raises(OidcTokenError, match="unknown signing key"):
            await oidc.validate_id_token(new_token, "n")
        assert idp.jwks_calls == 1

        clock.now += 2
        verified = await oidc.validate_id_token(new_token, "n")
        assert verified.subject == "kc-sub-1"
        assert idp.jwks_calls == 2
    finally:
        await oidc.aclose()


async def test_discovery_is_cached(settings: Settings, idp: FakeIdP) -> None:
    clock = Clock()
    oidc = OidcClient(settings.oidc, clock=clock)
    try:
        first = await oidc.metadata()
        moved = "https://elsewhere.example/token"
        idp.discovery["token_endpoint"] = moved
        assert await oidc.metadata() is first
        clock.now += settings.oidc.metadata_ttl_seconds
        assert (await oidc.metadata()).token_endpoint == moved
    finally:
        await oidc.aclose()


async def test_idp_without_s256_is_refused(settings: Settings, idp: FakeIdP) -> None:
    from spoldzielnia_ai.auth.oidc_client import OidcProviderError

    idp.discovery["code_challenge_methods_supported"] = ["plain"]
    oidc = OidcClient(settings.oidc)
    try:
        with pytest.raises(OidcProviderError, match="S256"):
            await oidc.metadata()
    finally:
        await oidc.aclose()
