"""Keycloak Admin REST API client for user management (Stage 8, chunk 8.1).

Authenticates as the `spoldzielnia-admin` service account (client credentials; realm-management
``view-users``, ``query-users``, ``manage-users`` only) on the internal URL. Only the calls the
user-management service needs are exposed. Request and response bodies are never logged: they
can carry a temporary password.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import httpx

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from spoldzielnia_ai.core.settings import KeycloakAdminSettings

log = get_logger(__name__)

# Refresh the service-account token this long before it expires.
_TOKEN_MARGIN_SECONDS = 30


class KeycloakAdminError(Exception):
    """Unexpected answer from Keycloak (→ 502). Messages are for logs only. ``key`` is
    Keycloak's error code for a 400 (e.g. ``invalidPasswordMinLengthMessage``)."""

    def __init__(self, message: str, key: str | None = None) -> None:
        super().__init__(message)
        self.key = key


class IdpUnavailable(KeycloakAdminError):
    """Keycloak is unreachable, timed out or not configured (→ 503)."""


class UserExists(KeycloakAdminError):
    """Username or e-mail already taken (→ 409)."""


class UserNotFound(KeycloakAdminError):
    """No such user in the realm (→ 404)."""


class PasswordRejected(KeycloakAdminError):
    """The realm's password policy refused a password. ``reason`` is Keycloak's message key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason, reason)
        self.reason = reason


class ActionEmailFailed(KeycloakAdminError):
    """Keycloak could not send the required-actions e-mail (no SMTP, no address)."""


@dataclass(frozen=True, slots=True)
class KcUser:
    id: str
    username: str
    email: str | None
    first_name: str | None
    last_name: str | None
    enabled: bool
    email_verified: bool
    required_actions: tuple[str, ...]
    # Keycloak reports whether an OTP credential is configured.
    totp: bool
    created_ms: int | None = None
    # Brokered identity providers (filled by `get_user`, empty in search results).
    identity_providers: tuple[str, ...] = field(default=())

    @property
    def display_name(self) -> str | None:
        name = " ".join(p for p in (self.first_name, self.last_name) if p)
        return name or None


def _user(raw: Mapping[str, Any], providers: Sequence[str] = ()) -> KcUser:
    actions = cast("list[object]", raw.get("requiredActions") or [])
    return KcUser(
        id=str(raw["id"]),
        username=str(raw.get("username", "")),
        email=raw.get("email") or None,
        first_name=raw.get("firstName") or None,
        last_name=raw.get("lastName") or None,
        enabled=bool(raw.get("enabled", False)),
        email_verified=bool(raw.get("emailVerified", False)),
        required_actions=tuple(str(a) for a in actions),
        totp=bool(raw.get("totp", False)),
        created_ms=raw.get("createdTimestamp"),
        identity_providers=tuple(providers),
    )


@dataclass(slots=True)
class _Token:
    value: str
    expires_at: float


class KeycloakAdmin:
    def __init__(
        self,
        settings: KeycloakAdminSettings,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()
        base = settings.base_url.rstrip("/")
        realm = quote(settings.realm, safe="")
        self._token_url = f"{base}/realms/{realm}/protocol/openid-connect/token"
        self._api = f"{base}/admin/realms/{realm}"

    @property
    def configured(self) -> bool:
        return self._settings.client_secret is not None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._settings.timeout_seconds,
                follow_redirects=False,
                headers={"Accept": "application/json"},
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --- Service-account token ------------------------------------------------------------

    async def _access_token(self, *, renew: bool = False) -> str:
        token = self._token
        if not renew and token and self._clock() < token.expires_at:
            return token.value
        async with self._token_lock:
            token = self._token
            if not renew and token and self._clock() < token.expires_at:
                return token.value
            secret = self._settings.client_secret
            if secret is None:
                raise IdpUnavailable("keycloak_admin.client_secret is not configured")
            try:
                response = await self._client().post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._settings.client_id,
                        "client_secret": secret.get_secret_value(),
                    },
                )
            except httpx.HTTPError as exc:
                raise IdpUnavailable(f"token request failed: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise KeycloakAdminError(f"token request refused: {response.status_code}")
            body = cast("dict[str, Any]", response.json())
            lifetime = int(body.get("expires_in", 60))
            self._token = _Token(
                str(body["access_token"]),
                self._clock() + max(lifetime - _TOKEN_MARGIN_SECONDS, 5),
            )
            return self._token.value

    # --- HTTP ---------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json: object = None,
        not_found: type[KeycloakAdminError] = UserNotFound,
    ) -> httpx.Response:
        url = self._api + path
        response: httpx.Response | None = None
        started = self._clock()
        for attempt in range(2):
            token = await self._access_token(renew=attempt > 0)
            try:
                response = await self._client().request(
                    method,
                    url,
                    params=dict(params) if params else None,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise IdpUnavailable(f"{method} failed: {type(exc).__name__}") from exc
            if response.status_code != 401:
                break
        if response is None:  # pragma: no cover - the loop runs at least once
            raise KeycloakAdminError("no response")
        log.debug(
            "keycloak_admin.request",
            method=method,
            status=response.status_code,
            ms=round((self._clock() - started) * 1000),
        )
        status = response.status_code
        if status < 300:
            return response
        if status == 404:
            raise not_found(f"{method} {path.split('/')[1]}: 404")
        if status == 409:
            raise UserExists("username or e-mail already exists")
        if status >= 500:
            raise IdpUnavailable(f"{method}: {status}")
        key = self._error_key(response) if status == 400 else None
        raise KeycloakAdminError(f"{method} {path.split('/')[1]}: {status}", key)

    @staticmethod
    def _error_key(response: httpx.Response) -> str:
        try:
            body = cast("dict[str, Any]", response.json())
        except ValueError:
            return "unknown"
        return str(body.get("error") or body.get("errorMessage") or "unknown")[:120]

    # --- Users --------------------------------------------------------------------------

    async def search_users(
        self, search: str = "", first: int = 0, maximum: int = 100
    ) -> list[KcUser]:
        params: dict[str, str | int | bool] = {
            "first": first,
            "max": maximum,
            "briefRepresentation": False,
        }
        if search:
            params["search"] = f"*{search.strip('*')}*"  # infix, not prefix-only
        response = await self._request("GET", "/users", params=params)
        return [_user(raw) for raw in cast("list[dict[str, Any]]", response.json())]

    async def get_user(self, user_id: str) -> KcUser:
        uid = quote(user_id, safe="")
        raw = cast("dict[str, Any]", (await self._request("GET", f"/users/{uid}")).json())
        links = (await self._request("GET", f"/users/{uid}/federated-identity")).json()
        providers = [str(link["identityProvider"]) for link in cast("list[dict[str, Any]]", links)]
        return _user(raw, providers)

    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        first_name: str | None,
        last_name: str | None,
        required_actions: Sequence[str],
    ) -> str:
        """Create an enabled user without credentials. Returns the Keycloak id."""
        body: dict[str, object] = {
            "username": username,
            "enabled": True,
            "emailVerified": False,
            "requiredActions": list(required_actions),
        }
        if email:
            body["email"] = email
        if first_name:
            body["firstName"] = first_name
        if last_name:
            body["lastName"] = last_name
        response = await self._request("POST", "/users", json=body)
        location = response.headers.get("location", "")
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not location or not user_id:
            raise KeycloakAdminError("create user: no Location header")
        return user_id

    async def update_user(
        self,
        user_id: str,
        *,
        enabled: bool | None = None,
        required_actions: Sequence[str] | None = None,
    ) -> None:
        """Partial update (Keycloak merges the fields that are present)."""
        body: dict[str, object] = {}
        if enabled is not None:
            body["enabled"] = enabled
        if required_actions is not None:
            body["requiredActions"] = list(required_actions)
        if body:
            await self._request("PUT", f"/users/{quote(user_id, safe='')}", json=body)

    async def delete_user(self, user_id: str) -> None:
        """Only to undo a half-finished create; the admin UI never deletes users."""
        await self._request("DELETE", f"/users/{quote(user_id, safe='')}")

    # --- Roles --------------------------------------------------------------------------

    async def realm_roles_of(self, user_id: str) -> set[str]:
        response = await self._request(
            "GET", f"/users/{quote(user_id, safe='')}/role-mappings/realm"
        )
        return {str(r["name"]) for r in cast("list[dict[str, Any]]", response.json())}

    async def _role_refs(
        self, user_id: str, names: Sequence[str], which: str
    ) -> list[dict[str, Any]]:
        """Role representations from the user's ``available`` or assigned (``""``) realm roles:
        the service account may map roles but cannot read the realm's role catalogue."""
        path = f"/users/{quote(user_id, safe='')}/role-mappings/realm"
        if which:
            path += f"/{which}"
        listed = cast("list[dict[str, Any]]", (await self._request("GET", path)).json())
        by_name = {str(r["name"]): r for r in listed}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise KeycloakAdminError(f"realm roles not {which or 'assigned'}: {missing}")
        return [{"id": by_name[n]["id"], "name": n} for n in names]

    async def add_realm_roles(self, user_id: str, names: Sequence[str]) -> None:
        if names:
            refs = await self._role_refs(user_id, names, "available")
            path = f"/users/{quote(user_id, safe='')}/role-mappings/realm"
            await self._request("POST", path, json=refs)

    async def remove_realm_roles(self, user_id: str, names: Sequence[str]) -> None:
        if names:
            refs = await self._role_refs(user_id, names, "")
            path = f"/users/{quote(user_id, safe='')}/role-mappings/realm"
            await self._request("DELETE", path, json=refs)

    # --- Credentials and sessions ---------------------------------------------------------

    async def set_temporary_password(self, user_id: str, password: str) -> None:
        path = f"/users/{quote(user_id, safe='')}/reset-password"
        try:
            await self._request(
                "PUT", path, json={"type": "password", "value": password, "temporary": True}
            )
        except KeycloakAdminError as exc:
            if type(exc) is not KeycloakAdminError or exc.key is None:
                raise
            raise PasswordRejected(exc.key) from None

    async def send_actions_email(self, user_id: str, actions: Sequence[str]) -> None:
        path = f"/users/{quote(user_id, safe='')}/execute-actions-email"
        params = {"lifespan": self._settings.reset_link_lifespan_seconds}
        try:
            await self._request("PUT", path, params=params, json=list(actions))
        except KeycloakAdminError as exc:
            if isinstance(exc, UserNotFound):
                raise
            # Keycloak answers 400/500 when the realm has no SMTP or the user has no e-mail.
            raise ActionEmailFailed(str(exc)) from exc

    async def logout(self, user_id: str) -> None:
        """End every Keycloak session of the user (refresh tokens stop working)."""
        await self._request("POST", f"/users/{quote(user_id, safe='')}/logout")
