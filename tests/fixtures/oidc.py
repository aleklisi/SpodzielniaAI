"""In-process fake OIDC provider: signing keys, JWKS/discovery/token endpoints via ``respx``."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx
from joserfc import jwt
from joserfc.jwk import ECKey, OctKey, RSAKey

if TYPE_CHECKING:
    import respx

ISSUER = "https://auth.spoldzielnia.localhost/realms/spoldzielnia"
CLIENT_ID = "spoldzielnia-backend"
CLIENT_SECRET = "oidc-secret"  # matches the `settings` fixture  # noqa: S105
AUTHORIZE_URL = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN_URL = f"{ISSUER}/protocol/openid-connect/token"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
REVOKE_URL = f"{ISSUER}/protocol/openid-connect/revoke"
LOGOUT_URL = f"{ISSUER}/protocol/openid-connect/logout"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"

_RSA_KEY = RSAKey.generate_key(2048, parameters={"kid": "rsa-1", "use": "sig", "alg": "RS256"})


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def unsigned_token(header: dict[str, Any], claims: dict[str, Any]) -> str:
    """A token with an arbitrary header and an empty signature (``alg=none`` attacks)."""
    return f"{_b64(header)}.{_b64(claims)}."


@dataclass
class FakeIdP:
    rsa: RSAKey = field(default_factory=lambda: _RSA_KEY)
    ec: ECKey = field(
        default_factory=lambda: ECKey.generate_key(
            "P-256", parameters={"kid": "ec-1", "use": "sig", "alg": "ES256"}
        )
    )
    # Keycloak publishes an encryption key next to the signing keys. It must never verify.
    enc: RSAKey = field(
        default_factory=lambda: RSAKey.generate_key(
            2048, parameters={"kid": "enc-1", "use": "enc", "alg": "RSA-OAEP"}
        )
    )
    published: list[RSAKey | ECKey] = field(default_factory=list[RSAKey | ECKey])
    discovery: dict[str, Any] = field(default_factory=dict[str, Any])
    id_token: str | None = None
    token_status: int = 200
    token_body: dict[str, Any] | None = None
    token_requests: list[httpx.Request] = field(default_factory=list[httpx.Request])
    jwks_calls: int = 0
    # Refresh grant, Keycloak-style rotation: every refresh issues a new token, and presenting
    # an already-used one is `invalid_grant`.
    refresh_counter: int = 0
    used_refresh_tokens: set[str] = field(default_factory=set[str])
    refresh_status: int = 200
    refresh_delay: float = 0.0
    refreshed_with: list[str] = field(default_factory=list[str])
    revoked: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        if not self.published:
            self.published = [self.rsa, self.ec, self.enc]
        if not self.discovery:
            self.discovery = {
                "issuer": ISSUER,
                "authorization_endpoint": AUTHORIZE_URL,
                "token_endpoint": TOKEN_URL,
                "jwks_uri": JWKS_URL,
                "end_session_endpoint": LOGOUT_URL,
                "revocation_endpoint": REVOKE_URL,
                "code_challenge_methods_supported": ["plain", "S256"],
                "id_token_signing_alg_values_supported": ["RS256", "ES256"],
            }

    def claims(self, **overrides: Any) -> dict[str, Any]:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": "kc-sub-1",
            "iat": now,
            "exp": now + 300,
            "auth_time": now,
            "nonce": "nonce",
            "email": "pszczelarz@example.org",
            "email_verified": True,
            "name": "Jan Pszczelarz",
            "preferred_username": "member1",
            "roles": ["MEMBER", "default-roles-spoldzielnia", "offline_access"],
            "acr": "1",
            "amr": ["pwd"],
        }
        claims.update(overrides)
        return {k: v for k, v in claims.items() if v is not None}

    def make_id_token(
        self,
        *,
        key: RSAKey | ECKey | OctKey | None = None,
        alg: str | None = None,
        header: dict[str, Any] | None = None,
        **claims: Any,
    ) -> str:
        key = key or self.rsa
        alg = alg or ("ES256" if isinstance(key, ECKey) else "RS256")
        base: dict[str, Any] = {"alg": alg, "typ": "JWT"}
        if key.kid:
            base["kid"] = key.kid
        return jwt.encode({**base, **(header or {})}, self.claims(**claims), key)

    def signing_copy(self, key: RSAKey, **params: Any) -> RSAKey:
        """The same key material with other JWK parameters (e.g. ``use=sig`` on the enc key)."""
        return RSAKey.import_key({**key.as_dict(private=True), **params})

    def mount(self, router: respx.MockRouter) -> None:
        router.get(DISCOVERY_URL).mock(side_effect=self._discovery)
        router.get(JWKS_URL).mock(side_effect=self._jwks)
        router.post(TOKEN_URL).mock(side_effect=self._token)
        router.post(REVOKE_URL).mock(side_effect=self._revoke)

    def _discovery(self, _request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.discovery)

    def _jwks(self, _request: httpx.Request) -> httpx.Response:
        self.jwks_calls += 1
        return httpx.Response(
            200, json={"keys": [k.as_dict(private=False) for k in self.published]}
        )

    def _revoke(self, request: httpx.Request) -> httpx.Response:
        self.revoked.append(self.form(request)["token"])
        return httpx.Response(200)

    async def _token(self, request: httpx.Request) -> httpx.Response:
        if self.form(request).get("grant_type") == "refresh_token":
            return await self._refresh(request)
        self.token_requests.append(request)
        if self.token_body is not None:
            return httpx.Response(self.token_status, json=self.token_body)
        return httpx.Response(
            self.token_status,
            json={
                "token_type": "Bearer",
                "access_token": "access-token",
                "refresh_token": f"rt-login-{len(self.token_requests)}",
                "expires_in": 900,
                "id_token": self.id_token,
            },
        )

    async def _refresh(self, request: httpx.Request) -> httpx.Response:
        presented = self.form(request)["refresh_token"]
        self.refreshed_with.append(presented)
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)  # widen the window for race tests
        if self.refresh_status != 200:
            return httpx.Response(self.refresh_status, json={"error": "temporarily_unavailable"})
        if presented in self.used_refresh_tokens:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.used_refresh_tokens.add(presented)
        self.refresh_counter += 1
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": f"access-{self.refresh_counter}",
                "refresh_token": f"rt-{self.refresh_counter}",
                "expires_in": 900,
            },
        )

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
