"""Request dependencies for authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status

from spoldzielnia_ai.auth.login_state import LoginStateStore
from spoldzielnia_ai.auth.oidc_client import OidcClient
from spoldzielnia_ai.auth.provisioning import DbUserProvisioner, UserProvisioner

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.core.settings import Settings


async def current_user_id() -> UUID:
    """Id of the authenticated user.

    Placeholder until cookie sessions land (1.6): every request is unauthenticated, so routes
    behind it fail closed with 401. Tests override it through ``app.dependency_overrides``.
    """
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def get_oidc_client(request: Request) -> OidcClient:
    return request.app.state.oidc_client


def get_login_state_store(request: Request) -> LoginStateStore:
    settings: Settings = request.app.state.settings
    return LoginStateStore(request.app.state.redis, settings.oidc.login_state_ttl_seconds)


def get_user_provisioner(request: Request) -> UserProvisioner:
    return DbUserProvisioner(request.app.state.session_factory)


OidcClientDep = Annotated[OidcClient, Depends(get_oidc_client)]
LoginStateStoreDep = Annotated[LoginStateStore, Depends(get_login_state_store)]
UserProvisionerDep = Annotated[UserProvisioner, Depends(get_user_provisioner)]
