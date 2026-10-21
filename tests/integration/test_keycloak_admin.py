"""Chunk 8.1: the user-management service account against the real realm (`make up`, then
`make keycloak-setup`). Keycloak's internal port is published on 127.0.0.1:8180 in dev only.

Covers U2 (the service account manages users and nothing else) and every client call.
"""

from __future__ import annotations

import json
import secrets
import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import SecretStr

from spoldzielnia_ai.auth.keycloak_admin import (
    KeycloakAdmin,
    KeycloakAdminError,
    PasswordRejected,
    UserExists,
    UserNotFound,
)
from spoldzielnia_ai.core.settings import KeycloakAdminSettings
from tests.integration.stack import REPO_ROOT, compose, require_port, secret

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

KC_PORT = 8180


@pytest.fixture
async def admin() -> AsyncIterator[KeycloakAdmin]:
    require_port(KC_PORT)
    settings = KeycloakAdminSettings(
        base_url=f"http://127.0.0.1:{KC_PORT}",
        client_secret=SecretStr(secret("keycloak_admin_client_secret")),
    )
    client = KeycloakAdmin(settings)
    try:
        await client.search_users(maximum=1)
    except KeycloakAdminError as exc:
        pytest.skip(f"service account not set up ({exc}): run `make keycloak-setup`")
    yield client
    await client.aclose()


@pytest.fixture
async def user_id(admin: KeycloakAdmin) -> AsyncIterator[str]:
    name = f"it-{secrets.token_hex(4)}"
    uid = await admin.create_user(
        username=name,
        email=f"{name}@example.test",
        first_name="Inte",
        last_name="Gration",
        required_actions=["UPDATE_PASSWORD"],
    )
    yield uid
    await admin.delete_user(uid)


async def test_user_lifecycle(admin: KeycloakAdmin, user_id: str) -> None:
    user = await admin.get_user(user_id)
    assert user.enabled
    assert user.required_actions == ("UPDATE_PASSWORD",)
    assert user.identity_providers == ()
    with pytest.raises(UserExists):
        await admin.create_user(
            username=user.username,
            email=None,
            first_name=None,
            last_name=None,
            required_actions=[],
        )
    assert [u.id for u in await admin.search_users(user.username[3:])] == [user_id]

    await admin.add_realm_roles(user_id, ["MEMBER", "EXPERT"])
    assert {"MEMBER", "EXPERT"} <= await admin.realm_roles_of(user_id)
    await admin.remove_realm_roles(user_id, ["EXPERT"])
    assert "EXPERT" not in await admin.realm_roles_of(user_id)

    with pytest.raises(PasswordRejected):
        await admin.set_temporary_password(user_id, "short")
    await admin.set_temporary_password(user_id, secrets.token_urlsafe(12))

    await admin.update_user(
        user_id, enabled=False, required_actions=["UPDATE_PASSWORD", "CONFIGURE_TOTP"]
    )
    user = await admin.get_user(user_id)
    assert not user.enabled
    assert set(user.required_actions) == {"UPDATE_PASSWORD", "CONFIGURE_TOTP"}
    await admin.logout(user_id)

    with pytest.raises(UserNotFound):
        await admin.get_user("00000000-0000-0000-0000-000000000000")


async def test_the_service_account_cannot_touch_the_realm(
    admin: KeycloakAdmin, user_id: str
) -> None:
    token = await admin._access_token()  # pyright: ignore[reportPrivateUsage]
    headers = {"Authorization": f"Bearer {token}"}
    api = f"http://127.0.0.1:{KC_PORT}/admin/realms/spoldzielnia"
    async with httpx.AsyncClient(headers=headers) as http:
        assert (await http.put(api, json={"displayName": "pwned"})).status_code == 403
        assert (await http.get(f"{api}/clients")).status_code == 403
        # Reading role names is allowed (needed to map them); creating roles is not.
        assert (await http.post(f"{api}/roles", json={"name": "pwned"})).status_code == 403
        assert (await http.post(f"{api}/clients", json={"clientId": "x"})).status_code == 403
    # Only the three application roles are ever mappable through the client.
    with pytest.raises(KeycloakAdminError, match="not available"):
        await admin.add_realm_roles(user_id, ["realm-admin"])


def test_setup_is_idempotent_and_grants_exactly_three_roles() -> None:
    require_port(KC_PORT)
    for _ in range(2):
        run = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(REPO_ROOT / "infra" / "scripts" / "keycloak-setup.sh")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert run.returncode == 0, run.stderr
    mappings = compose(
        "exec",
        "-T",
        "keycloak",
        "bash",
        "-c",
        "cfg=/tmp/kcadm.config; /opt/keycloak/bin/kcadm.sh config credentials --config $cfg"
        " --server http://localhost:8080 --realm master --user admin"
        ' --password "$(< /run/secrets/keycloak_admin_password)" >/dev/null 2>&1;'
        " /opt/keycloak/bin/kcadm.sh get-roles --config $cfg -r spoldzielnia"
        " --uusername service-account-spoldzielnia-admin --cclientid realm-management",
    )
    assert mappings.returncode == 0, mappings.stderr
    names = {role["name"] for role in json.loads(mappings.stdout)}
    assert names == {"view-users", "query-users", "manage-users"}
