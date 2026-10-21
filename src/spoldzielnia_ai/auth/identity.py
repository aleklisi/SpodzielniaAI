"""What the application takes from a verified ID token."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from spoldzielnia_ai.db.enums import Role

if TYPE_CHECKING:
    from collections.abc import Mapping

    from spoldzielnia_ai.auth.oidc_client import VerifiedIdToken

_ROLE_VALUES = {role.value: role for role in Role}


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    issuer: str
    subject: str
    email: str | None
    display_name: str | None
    roles: tuple[Role, ...]
    acr: str | None
    amr: tuple[str, ...]
    auth_time: int | None
    username: str | None = None


def _claim_at(claims: Mapping[str, Any], path: str) -> object:
    node: object = claims
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = cast("dict[str, object]", node).get(part)
    return node


def extract_roles(claims: Mapping[str, Any], path: str) -> tuple[Role, ...]:
    """Application roles at the dotted ``path``. Unknown entries (Keycloak's
    ``default-roles-*``, ``offline_access`` …) are ignored, and so is a malformed claim."""
    raw = _claim_at(claims, path)
    if not isinstance(raw, list):
        return ()
    found: dict[Role, None] = {}
    for item in cast("list[object]", raw):
        if isinstance(item, str) and item in _ROLE_VALUES:
            found[_ROLE_VALUES[item]] = None
    return tuple(found)


def _str(claims: Mapping[str, Any], name: str) -> str | None:
    value = claims.get(name)
    return value if isinstance(value, str) and value else None


def identity_from_token(token: VerifiedIdToken, roles_claim: str) -> OidcIdentity:
    claims = token.claims
    amr_claim = claims.get("amr")
    amr = (
        tuple(m for m in cast("list[object]", amr_claim) if isinstance(m, str))
        if isinstance(amr_claim, list)
        else ()
    )
    auth_time = claims.get("auth_time")
    return OidcIdentity(
        issuer=token.issuer,
        subject=token.subject,
        # An unverified address is not an identity attribute we can rely on.
        email=_str(claims, "email") if claims.get("email_verified") is True else None,
        display_name=_str(claims, "name") or _str(claims, "preferred_username"),
        roles=extract_roles(claims, roles_claim),
        acr=_str(claims, "acr"),
        amr=amr,
        auth_time=auth_time if type(auth_time) is int else None,  # bool is an int subclass
        username=_str(claims, "preferred_username"),
    )
