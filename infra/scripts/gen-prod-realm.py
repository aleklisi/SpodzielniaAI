#!/usr/bin/env python3
"""Write the production realm import from the dev export (6.8): `make prod-realm`.

The dev export (infra/keycloak/realm-spoldzielnia.json) stays the one source of the realm's
security settings: flows, OTP policy, brute-force protection, token lifetimes, clients, roles.
The production copy differs only in what must not reach a server:

- no users but the `spoldzielnia-admin` service account (dev test users and their password and
  TOTP placeholders are dropped; the first admin is created with `make create-admin`);
- hostnames are `${SITE_DOMAIN}`, the mail server `${SMTP_HOST}`, `${SMTP_PORT}` and
  `${SMTP_FROM}`, resolved by Keycloak from its environment at import (compose.prod.yaml);
- self-registration stays off and HTTPS is required for every client.

Keycloak imports a realm only if it does not exist yet, so the file matters on the first start
of a server only. Plain python3 (no venv), so it also runs on servers. A unit test keeps the
committed file in step with the dev export.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "infra/keycloak/realm-spoldzielnia.json"
PROD = ROOT / "infra/keycloak/realm-spoldzielnia.prod.json"
DEV_HOST = "spoldzielnia.localhost"
SERVICE_ACCOUNT = "service-account-spoldzielnia-admin"


def _swap_host(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(DEV_HOST, "${SITE_DOMAIN}")
    if isinstance(value, list):
        return [_swap_host(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: _swap_host(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def prod_realm(dev: dict[str, Any]) -> dict[str, Any]:
    realm = dict(dev)
    realm["users"] = [u for u in dev.get("users", []) if u.get("username") == SERVICE_ACCOUNT]
    realm["registrationAllowed"] = False
    realm["sslRequired"] = "all"
    # SMTP credentials are not part of the import (they would sit in the container's
    # environment): an admin enters them in the Keycloak console, which stores them in its DB.
    realm["smtpServer"] = {
        "host": "${SMTP_HOST}",
        "port": "${SMTP_PORT}",
        "from": "${SMTP_FROM}",
        "fromDisplayName": dev.get("smtpServer", {}).get("fromDisplayName", "Spółdzielnia AI"),
        "starttls": "true",
    }
    return _swap_host(realm)


def render() -> str:
    return json.dumps(prod_realm(json.loads(DEV.read_text())), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if PROD.read_text() != text:
            print(f"{PROD.relative_to(ROOT)} is stale: run `make prod-realm`", file=sys.stderr)
            return 1
        return 0
    PROD.write_text(text)
    print(f"wrote {PROD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
