"""OpenID Connect relying party for the BFF (confidential client, Authorization Code + PKCE).

One trusted issuer. Discovery metadata and the JWKS are cached in-process. An unknown ``kid``
triggers a JWKS refetch, rate-limited so forged tokens cannot hammer the IdP. ID tokens are
validated per OIDC Core §3.1.3.7: the signature uses an allow-listed asymmetric ``alg``, plus
``iss``, ``aud``/``azp``, ``exp``/``iat`` (with leeway), ``nonce`` and ``sub``.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import httpx
from joserfc import jws, jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from pydantic import BaseModel, ConfigDict, ValidationError

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from joserfc.jwk import ECKey, RSAKey

    from spoldzielnia_ai.core.settings import OidcSettings

log = get_logger(__name__)

_SIGNING_KEY_TYPES = frozenset({"RSA", "EC"})


class OidcError(Exception):
    """Base class. Messages are for logs only and never sent to the client."""


class OidcProviderError(OidcError):
    """The IdP is unreachable, misconfigured or answered unexpectedly (→ 502)."""


class OidcTokenError(OidcError):
    """The authorization code or ID token was rejected (→ 401)."""


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None
    revocation_endpoint: str | None = None
    code_challenge_methods_supported: list[str] = []


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    token_type: str
    access_token: str
    id_token: str | None = None  # required for the code grant, optional on refresh
    refresh_token: str | None = None
    expires_in: int | None = None
    refresh_expires_in: int | None = None
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedIdToken:
    issuer: str
    subject: str
    claims: Mapping[str, Any]


@dataclass(slots=True)
class _Cached[T]:
    value: T
    fetched_at: float


class OidcClient:
    def __init__(self, settings: OidcSettings, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings
        self._clock = clock
        self._http: httpx.AsyncClient | None = None
        self._metadata: _Cached[ProviderMetadata] | None = None
        self._jwks: _Cached[KeySet] | None = None
        # Separate locks: refreshing the JWKS may need the metadata.
        self._metadata_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    # --- HTTP ---------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        """Created on first use: in dev the CA bundle appears only after the proxy has started."""
        if self._http is None:
            verify: ssl.SSLContext | bool = True
            if self._settings.ca_bundle:
                try:
                    verify = ssl.create_default_context(cafile=self._settings.ca_bundle)
                except (OSError, ssl.SSLError) as exc:
                    raise OidcProviderError(f"cannot load oidc.ca_bundle: {exc}") from exc
            self._http = httpx.AsyncClient(
                timeout=self._settings.http_timeout_seconds,
                verify=verify,
                follow_redirects=False,
                headers={"Accept": "application/json"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get_json(self, url: str) -> object:
        try:
            response = await self._client().get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcProviderError(f"GET {url} failed: {type(exc).__name__}") from exc

    # --- Discovery & keys ---------------------------------------------------------------

    def _metadata_fresh(self) -> ProviderMetadata | None:
        cached = self._metadata
        if cached and self._clock() - cached.fetched_at < self._settings.metadata_ttl_seconds:
            return cached.value
        return None

    async def metadata(self) -> ProviderMetadata:
        if fresh := self._metadata_fresh():
            return fresh
        async with self._metadata_lock:
            if fresh := self._metadata_fresh():
                return fresh
            url = self._settings.issuer.rstrip("/") + "/.well-known/openid-configuration"
            try:
                metadata = ProviderMetadata.model_validate(await self._get_json(url))
            except ValidationError as exc:
                raise OidcProviderError("invalid discovery document") from exc
            # Mix-up protection (OIDC Discovery §4.3): the document must describe our issuer.
            if metadata.issuer != self._settings.issuer:
                raise OidcProviderError(f"discovery issuer mismatch: {metadata.issuer!r}")
            methods = metadata.code_challenge_methods_supported
            if methods and "S256" not in methods:
                raise OidcProviderError("IdP does not support PKCE S256")
            self._metadata = _Cached(metadata, self._clock())
            return metadata

    async def _fetch_jwks(self) -> KeySet:
        document = await self._get_json((await self.metadata()).jwks_uri)
        doc = cast("dict[str, object]", document) if isinstance(document, dict) else {}
        raw_keys = doc.get("keys")
        if not isinstance(raw_keys, list):
            raise OidcProviderError("invalid JWKS document")
        # Signing keys only: Keycloak also publishes an RSA-OAEP encryption key.
        keys: list[dict[str, Any]] = []
        for raw in cast("list[object]", raw_keys):
            if not isinstance(raw, dict):
                continue
            key = cast("dict[str, Any]", raw)
            if key.get("kty") in _SIGNING_KEY_TYPES and key.get("use", "sig") == "sig":
                keys.append(key)
        try:
            return KeySet.import_key_set({"keys": keys})
        except (JoseError, ValueError) as exc:
            raise OidcProviderError("unusable JWKS") from exc

    async def _signing_key(self, kid: str) -> RSAKey | ECKey:
        for attempt in range(2):
            cached = self._jwks
            stale = cached is None or (
                attempt == 1
                and self._clock() - cached.fetched_at >= self._settings.jwks_min_refresh_seconds
            )
            if stale:
                async with self._jwks_lock:
                    if self._jwks is cached:  # nobody refreshed while we waited
                        self._jwks = _Cached(await self._fetch_jwks(), self._clock())
                cached = self._jwks
            if cached is None:
                raise OidcProviderError("JWKS unavailable")
            try:
                key = cached.value.get_by_kid(kid)
            except InvalidKeyIdError:
                continue
            return cast("RSAKey | ECKey", key)
        raise OidcTokenError(f"unknown signing key id {kid!r}")

    # --- Flow ---------------------------------------------------------------------------

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        acr_values: str | None = None,
        prompt: str | None = None,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "scope": " ".join(self._settings.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if acr_values:
            params["acr_values"] = acr_values
        if prompt:
            params["prompt"] = prompt
        endpoint = httpx.URL((await self.metadata()).authorization_endpoint)
        return str(endpoint.copy_merge_params(params))

    def _client_auth(self) -> httpx.BasicAuth:
        # client_secret_basic: id and secret are form-urlencoded before base64 (RFC 6749 §2.3.1).
        return httpx.BasicAuth(
            quote(self._settings.client_id, safe=""),
            quote(self._settings.client_secret.get_secret_value(), safe=""),
        )

    async def _token_request(self, data: dict[str, str]) -> TokenResponse:
        token_endpoint = (await self.metadata()).token_endpoint
        try:
            response = await self._client().post(
                token_endpoint, data=data, auth=self._client_auth()
            )
        except httpx.HTTPError as exc:
            raise OidcProviderError(f"token request failed: {type(exc).__name__}") from exc
        if response.status_code in (400, 401):
            raise OidcTokenError(
                f"token endpoint rejected the {data['grant_type']} grant: {_oauth_error(response)}"
            )
        if response.status_code != 200:
            raise OidcProviderError(f"token endpoint returned {response.status_code}")
        try:
            tokens = TokenResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise OidcProviderError("invalid token response") from exc
        if tokens.token_type.lower() != "bearer":
            raise OidcProviderError(f"unexpected token_type {tokens.token_type!r}")
        return tokens

    async def exchange_code(self, code: str, code_verifier: str) -> TokenResponse:
        tokens = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._settings.redirect_uri,
                "code_verifier": code_verifier,
            }
        )
        if not tokens.id_token:
            raise OidcProviderError("token response has no id_token")
        return tokens

    async def refresh(self, refresh_token: str) -> TokenResponse:
        """Refresh-token grant. ``OidcTokenError`` means the IdP ended the session (revoked,
        expired, idle, or reused): the caller must drop the session."""
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def revoke(self, refresh_token: str) -> None:
        """RFC 7009 revocation, best effort: logout must not fail because the IdP is down."""
        try:
            endpoint = (await self.metadata()).revocation_endpoint
            if endpoint is None:
                return
            await self._client().post(
                endpoint,
                data={"token": refresh_token, "token_type_hint": "refresh_token"},
                auth=self._client_auth(),
            )
        except (OidcError, httpx.HTTPError) as exc:
            log.warning("oidc.revoke_failed", error=type(exc).__name__)

    async def end_session_url(self, id_token_hint: str | None) -> str | None:
        """RP-initiated logout URL for the browser, if the IdP offers one."""
        try:
            endpoint = (await self.metadata()).end_session_endpoint
        except OidcError:
            return None
        if endpoint is None:
            return None
        params = {
            "client_id": self._settings.client_id,
            "post_logout_redirect_uri": self._settings.post_logout_redirect_uri,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return str(httpx.URL(endpoint).copy_merge_params(params))

    async def validate_id_token(self, id_token: str, nonce: str) -> VerifiedIdToken:
        try:
            header = jws.extract_compact(id_token.encode("ascii")).headers()
        except (JoseError, ValueError, UnicodeError) as exc:
            raise OidcTokenError("malformed ID token") from exc
        alg, kid = header.get("alg"), header.get("kid")
        # Checked before key lookup: `none` / `HS*` never reach a verifier.
        if alg not in self._settings.signing_algs:
            raise OidcTokenError(f"disallowed ID token alg {alg!r}")
        if not isinstance(kid, str) or not kid:
            raise OidcTokenError("ID token has no kid")

        key = await self._signing_key(kid)
        try:
            token = jwt.decode(id_token, key, algorithms=[alg])
            registry = jwt.JWTClaimsRegistry(
                leeway=self._settings.leeway_seconds,
                iss={"essential": True, "value": self._settings.issuer},
                aud={"essential": True, "value": self._settings.client_id},
                sub={"essential": True},
                exp={"essential": True},
                iat={"essential": True},
                nonce={"essential": True, "value": nonce},
            )
            registry.validate(token.claims)
        except (JoseError, ValueError) as exc:
            raise OidcTokenError(f"ID token rejected: {type(exc).__name__}: {exc}") from exc

        claims = token.claims
        aud: object = claims["aud"]
        audiences = cast("list[object]", aud) if isinstance(aud, list) else [aud]
        azp = claims.get("azp")
        if (len(audiences) > 1 or azp is not None) and azp != self._settings.client_id:
            raise OidcTokenError("ID token azp is not this client")
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise OidcTokenError("ID token sub is empty")
        return VerifiedIdToken(issuer=self._settings.issuer, subject=subject, claims=claims)


def _oauth_error(response: httpx.Response) -> str:
    """The OAuth ``error`` code only (never the description, which may echo input)."""
    try:
        body = response.json()
    except ValueError:
        return "unparseable"
    error = cast("dict[str, Any]", body).get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and error.isascii() and len(error) <= 64 else "unknown"
