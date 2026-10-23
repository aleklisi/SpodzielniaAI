"""Production deployment files (6.8): the realm import a server gets, and the prod compose set."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROD_REALM = ROOT / "infra/keycloak/realm-spoldzielnia.prod.json"
DEV_REALM = ROOT / "infra/keycloak/realm-spoldzielnia.json"
PROD_ENV = {
    "REGISTRY": "ghcr.io/example/spoldzielnia",
    "VERSION": "v1.2.3",
    "SITE_DOMAIN": "coop.example.org",
    "ACME_EMAIL": "ops@example.org",
}


def _realm() -> dict[str, Any]:
    return json.loads(PROD_REALM.read_text())


def test_committed_prod_realm_matches_the_dev_export() -> None:
    result = subprocess.run(  # noqa: S603 - fixed script path
        [sys.executable, str(ROOT / "infra/scripts/gen-prod-realm.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_prod_realm_has_no_test_users_or_their_placeholders() -> None:
    text = PROD_REALM.read_text()
    assert [u["username"] for u in _realm()["users"]] == ["service-account-spoldzielnia-admin"]
    for leak in ("DEV_USER_PASSWORD", "DEV_TOTP_SEED", "localhost", "mailpit"):
        assert leak not in text


def test_prod_realm_keeps_the_dev_security_settings() -> None:
    dev, prod = json.loads(DEV_REALM.read_text()), _realm()
    for key in (
        "bruteForceProtected",
        "passwordPolicy",
        "otpPolicyType",
        "browserFlow",
        "authenticationFlows",
        "roles",
        "revokeRefreshToken",
        "ssoSessionIdleTimeout",
    ):
        assert prod[key] == dev[key], key
    assert prod["registrationAllowed"] is False
    assert prod["sslRequired"] == "all"


def test_prod_realm_client_points_at_the_public_domain() -> None:
    backend = next(c for c in _realm()["clients"] if c["clientId"] == "spoldzielnia-backend")
    assert backend["redirectUris"] == ["https://${SITE_DOMAIN}/api/auth/callback"]
    assert backend["webOrigins"] == ["https://${SITE_DOMAIN}"]
    assert backend["secret"] == "${OIDC_CLIENT_SECRET}"  # noqa: S105 - a placeholder
    assert backend["attributes"]["pkce.code.challenge.method"] == "S256"


def _compose_config(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    files = ["-f", "compose.yaml", "-f", "compose.prod.yaml", "--profile", "ops"]
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["docker", "compose", "--env-file", os.devnull, *files, "config", "--format", "json"],  # noqa: S607
        cwd=ROOT,
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), **env},
        capture_output=True,
        text=True,
        check=False,
    )


needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="no docker CLI")


@needs_compose
def test_prod_compose_refuses_to_start_without_its_settings() -> None:
    result = _compose_config({})
    assert result.returncode != 0
    for name in ("REGISTRY", "SITE_DOMAIN", "ACME_EMAIL"):
        assert f"required variable {name}" in result.stderr


@needs_compose
def test_prod_compose_runs_registry_images_with_acme_and_limits() -> None:
    result = _compose_config(PROD_ENV)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("api", "worker-core", "worker-media", "frontend", "migrate", "beat"):
        assert services[name]["image"].startswith("ghcr.io/example/spoldzielnia/"), name
        assert services[name]["image"].endswith(":v1.2.3"), name
        assert "build" not in services[name], name
    for name, service in services.items():
        if name in ("migrate", "bootstrap", "backup", "models-init", "mailpit", "llm", "llm-relay"):
            continue
        assert service["restart"] == "unless-stopped", name
        assert service.get("mem_limit"), name
        assert service["logging"]["options"]["max-size"], name
    assert services["proxy"]["environment"]["CADDY_TLS"] == "ops@example.org"
    assert services["keycloak"]["environment"]["KC_HOSTNAME"] == "https://auth.coop.example.org"
    assert "--import-realm" in services["keycloak"]["environment"]["KC_COMMAND"]
    assert [p["published"] for p in services["proxy"]["ports"]] == ["80", "443"]
    assert [n for n, s in services.items() if s.get("ports")] == ["proxy"]
