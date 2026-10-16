"""Chunk 1.5: real browser-style login against the running dev stack (`make up`).

Drives Keycloak's HTML forms with httpx: login → IdP form → (OTP) → callback. Needs the stack,
`secrets/` and Caddy's local CA. Skipped otherwise.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import re
import ssl
import struct
import time
from typing import TYPE_CHECKING

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.stack import compose, require_port, secret

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

SITE = "https://spoldzielnia.localhost"
FORM_ACTION = r'<form[^>]*id="{form_id}"[^>]*action="([^"]+)"'


def _totp(seed: str, at: float | None = None) -> str:
    """RFC 6238 (HMAC-SHA1, 6 digits, 30 s). Keycloak keys the HMAC with the raw secret bytes."""
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(seed.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def test_totp_rfc6238_vector() -> None:
    # RFC 6238 Appendix B, SHA-1 seed, T = 59 s → 94287082 (8 digits); 6-digit tail.
    assert _totp("12345678901234567890", at=59) == "287082"


@pytest.fixture(scope="module")
def caddy_ca(tmp_path_factory: pytest.TempPathFactory) -> Path:
    require_port(443)
    path = tmp_path_factory.mktemp("ca") / "caddy-root.crt"
    result = compose("cp", "proxy:/data/caddy/pki/authorities/local/root.crt", str(path))
    if result.returncode != 0:
        pytest.skip(f"cannot read Caddy's root CA: {result.stderr.strip()}")
    return path


def _form_action(page: httpx.Response, form_id: str) -> str:
    match = re.search(FORM_ACTION.format(form_id=form_id), page.text)
    assert match, f"no #{form_id} in Keycloak page (status {page.status_code})"
    return html.unescape(match.group(1))


def _login(caddy_ca: Path, username: str, *, otp_seed: str | None = None) -> httpx.Response:
    password = secret("dev_user_password")
    with httpx.Client(
        verify=ssl.create_default_context(cafile=caddy_ca), follow_redirects=False, timeout=20
    ) as client:
        start = client.get(f"{SITE}/api/auth/login", params={"return_to": "/wiki?x=1"})
        assert start.status_code == 302
        form = client.get(start.headers["location"])
        step = client.post(
            _form_action(form, "kc-form-login"),
            data={"username": username, "password": password, "credentialId": ""},
        )
        if otp_seed is not None:
            assert step.status_code == 200, "expected the OTP form"
            otp_form = _form_action(step, "kc-otp-login-form")
            step = client.post(otp_form, data={"otp": _totp(otp_seed)})
            if step.status_code == 200:
                # Keycloak refuses a code already used (an earlier run in the same 30 s window):
                # wait for the next window and try once more.
                time.sleep(30 - time.time() % 30 + 1)
                otp_form = _form_action(step, "kc-otp-login-form")
                step = client.post(otp_form, data={"otp": _totp(otp_seed)})
        assert step.status_code == 302, step.text[:500]
        assert step.headers["location"].startswith(f"{SITE}/api/auth/callback?")
        return client.get(step.headers["location"])


async def _db_user(email: str) -> tuple[list[str], list[str]]:
    """(roles, amr of the latest login) for the user with ``email``, read as the app role."""
    url = URL.create(
        "postgresql+asyncpg",
        username="spoldzielnia_app",
        password=secret("pg_app_password"),
        host="127.0.0.1",
        port=5432,
        database="spoldzielnia",
    )
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT u.roles::text[] AS roles, l.payload -> 'amr' AS amr "
                        "FROM app_user u JOIN LATERAL ("
                        "  SELECT payload FROM security_audit_log "
                        "  WHERE actor_id = u.id AND event_type = 'USER_LOGIN' "
                        "  ORDER BY id DESC LIMIT 1) l ON true "
                        "WHERE u.email = :email"
                    ),
                    {"email": email},
                )
            ).one()
            return list(row.roles), list(row.amr)
    finally:
        await engine.dispose()


def test_member_password_login(caddy_ca: Path) -> None:
    callback = _login(caddy_ca, "member1")
    assert callback.status_code == 303, callback.text
    assert callback.headers["location"] == "/wiki?x=1"
    roles, amr = asyncio.run(_db_user("member1@spoldzielnia.localhost"))
    assert roles == ["MEMBER"]
    assert amr == ["pwd"]


def test_expert_login_with_otp_reports_amr(caddy_ca: Path) -> None:
    callback = _login(caddy_ca, "expert1", otp_seed=secret("dev_totp_seed"))
    assert callback.status_code == 303, callback.text
    roles, amr = asyncio.run(_db_user("expert1@spoldzielnia.localhost"))
    assert sorted(roles) == ["EXPERT", "MEMBER"]
    assert set(amr) >= {"pwd", "otp"}
