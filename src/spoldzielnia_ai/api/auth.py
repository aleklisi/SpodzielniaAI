"""``/auth``: OIDC login for the Backend-for-Frontend.

The browser only ever sees redirects and cookies. Tokens stay on the server. ``state`` is bound
to the browser that started the login by a short-lived ``__Host-`` cookie, so a callback URL
planted by someone else (login CSRF) is refused.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Cookie, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from spoldzielnia_ai.auth.deps import (  # noqa: TC001 - FastAPI resolves dependency annotations
    LoginStateStoreDep,
    OidcClientDep,
    UserProvisionerDep,
)
from spoldzielnia_ai.auth.identity import identity_from_token
from spoldzielnia_ai.auth.login_state import LoginState
from spoldzielnia_ai.auth.oidc_client import OidcProviderError, OidcTokenError
from spoldzielnia_ai.auth.pkce import LoginSecrets
from spoldzielnia_ai.auth.redirects import safe_return_to
from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_COOKIE = "__Host-oidc_login"
ALLOWED_PROMPTS = frozenset({"login"})


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _error(status_code: int, detail: str) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    _clear_login_cookie(response)
    return response


def _clear_login_cookie(response: Response) -> None:
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


@router.get("/login", status_code=status.HTTP_302_FOUND, response_model=None)
async def login(
    request: Request,
    oidc: OidcClientDep,
    store: LoginStateStoreDep,
    return_to: str | None = None,
    provider: str | None = None,
    acr_values: str | None = None,
    prompt: str | None = None,
) -> Response:
    """Start Authorization Code + PKCE. ``acr_values``/``prompt`` serve step-up MFA (1.6)."""
    settings = _settings(request)
    if provider is not None and provider != settings.oidc.provider:
        return _error(status.HTTP_400_BAD_REQUEST, "Unknown identity provider")
    if acr_values is not None and not set(acr_values.split()) <= set(
        settings.security.mfa_acr_values
    ):
        return _error(status.HTTP_400_BAD_REQUEST, "Unsupported acr_values")
    if prompt is not None and prompt not in ALLOWED_PROMPTS:
        return _error(status.HTTP_400_BAD_REQUEST, "Unsupported prompt")

    secrets = LoginSecrets.generate()
    try:
        url = await oidc.authorization_url(
            state=secrets.state,
            nonce=secrets.nonce,
            code_challenge=secrets.code_challenge,
            acr_values=acr_values,
            prompt=prompt,
        )
    except OidcProviderError as exc:
        log.error("auth.login.provider_error", error=str(exc))
        return _error(status.HTTP_502_BAD_GATEWAY, "Identity provider unavailable")

    await store.save(
        secrets.state,
        LoginState(
            code_verifier=secrets.code_verifier,
            nonce=secrets.nonce,
            return_to=safe_return_to(return_to, settings.security.allowed_origins),
        ),
    )
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    # Lax, not Strict: the callback is a cross-site top-level navigation from the IdP.
    response.set_cookie(
        LOGIN_COOKIE,
        secrets.state,
        max_age=settings.oidc.login_state_ttl_seconds,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback", status_code=status.HTTP_303_SEE_OTHER, response_model=None)
async def callback(
    request: Request,
    oidc: OidcClientDep,
    store: LoginStateStoreDep,
    provisioner: UserProvisionerDep,
    login_cookie: Annotated[str | None, Cookie(alias=LOGIN_COOKIE)] = None,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> Response:
    settings = _settings(request)
    if not state or not login_cookie or not hmac.compare_digest(state, login_cookie):
        log.warning("auth.callback.state_not_bound")
        return _error(status.HTTP_400_BAD_REQUEST, "Invalid login state")
    login_state = await store.consume(state)
    if login_state is None:
        log.warning("auth.callback.state_unknown_or_replayed")
        return _error(status.HTTP_400_BAD_REQUEST, "Invalid login state")
    if error is not None:
        log.info("auth.callback.provider_error", error=error[:64])
        return _error(status.HTTP_400_BAD_REQUEST, "Login was not completed")
    if not code:
        return _error(status.HTTP_400_BAD_REQUEST, "Missing authorization code")

    try:
        tokens = await oidc.exchange_code(code, login_state.code_verifier)
        verified = await oidc.validate_id_token(tokens.id_token, login_state.nonce)
    except OidcTokenError as exc:
        log.warning("auth.callback.token_rejected", error=str(exc))
        return _error(status.HTTP_401_UNAUTHORIZED, "Login failed")
    except OidcProviderError as exc:
        log.error("auth.callback.provider_error", error=str(exc))
        return _error(status.HTTP_502_BAD_GATEWAY, "Identity provider unavailable")

    identity = identity_from_token(verified, settings.oidc.roles_claim)
    user = await provisioner.provision(identity, settings.oidc.provider)
    if user.is_locked:
        log.warning("auth.callback.user_locked", user_id=str(user.id))
        return _error(status.HTTP_423_LOCKED, "Account locked")

    log.info("auth.login", user_id=str(user.id), created=user.created)
    # TODO(1.6): issue the `__Host-sid` session and keep `tokens` server-side.
    response = RedirectResponse(login_state.return_to, status_code=status.HTTP_303_SEE_OTHER)
    _clear_login_cookie(response)
    return response
