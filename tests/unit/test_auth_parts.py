"""Chunk 1.5: PKCE, return_to sanitising, claim mapping, settings guards, no stored passwords."""

from __future__ import annotations

import base64
import hashlib
import re

import pytest
from pydantic import SecretStr, ValidationError

from spoldzielnia_ai.auth.identity import extract_roles, identity_from_token
from spoldzielnia_ai.auth.oidc_client import VerifiedIdToken
from spoldzielnia_ai.auth.pkce import LoginSecrets, code_challenge_s256
from spoldzielnia_ai.auth.redirects import safe_return_to
from spoldzielnia_ai.core.settings import OidcSettings
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import Base

ORIGINS = ["https://spoldzielnia.localhost"]


def test_pkce_verifier_and_challenge() -> None:
    first, second = LoginSecrets.generate(), LoginSecrets.generate()
    assert re.fullmatch(r"[A-Za-z0-9_-]{43,128}", first.code_verifier)
    assert len({first.state, second.state, first.nonce, second.nonce}) == 4
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(first.code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert first.code_challenge == expected


def test_rfc7636_appendix_b_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/", "/"),
        ("/wiki/a?b=1#c", "/wiki/a?b=1#c"),
        ("https://spoldzielnia.localhost", "/"),
        ("https://SPOLDZIELNIA.localhost/me", "/me"),
        ("https://spoldzielnia.localhost//evil.example", "/"),
        ("https://spoldzielnia.localhost.evil.example/", "/"),
        ("https://spoldzielnia.localhost@evil.example/", "/"),
        ("http://spoldzielnia.localhost/", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("relative/path", "/"),
        ("/a\x00b", "/"),
        ("/" + "a" * 3000, "/"),
    ],
)
def test_safe_return_to(value: str | None, expected: str) -> None:
    assert safe_return_to(value, ORIGINS) == expected


@pytest.mark.parametrize(
    ("claims", "path", "expected"),
    [
        ({"roles": ["EXPERT", "MEMBER", "EXPERT"]}, "roles", (Role.EXPERT, Role.MEMBER)),
        ({"roles": ["admin", "offline_access"]}, "roles", ()),
        ({"roles": "ADMIN"}, "roles", ()),
        ({"roles": [1, None, "ADMIN"]}, "roles", (Role.ADMIN,)),
        ({"realm_access": {"roles": ["ADMIN"]}}, "realm_access.roles", (Role.ADMIN,)),
        ({"realm_access": ["ADMIN"]}, "realm_access.roles", ()),
        ({}, "roles", ()),
    ],
)
def test_extract_roles(claims: dict[str, object], path: str, expected: tuple[Role, ...]) -> None:
    assert extract_roles(claims, path) == expected


def test_identity_ignores_unverified_email_and_odd_types() -> None:
    token = VerifiedIdToken(
        issuer="https://idp",
        subject="s",
        claims={
            "email": "a@b.c",
            "email_verified": False,
            "preferred_username": "member1",
            "amr": ["pwd", 7, "otp"],
            "auth_time": True,
            "acr": 1,
        },
    )
    identity = identity_from_token(token, "roles")
    assert identity.email is None
    assert identity.display_name == "member1"
    assert identity.amr == ("pwd", "otp")
    assert identity.auth_time is None
    assert identity.acr is None
    assert identity.roles == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"signing_algs": ["HS256"]},
        {"signing_algs": ["none"]},
        {"signing_algs": []},
        {"scopes": ["profile"]},
    ],
)
def test_oidc_settings_refuse_unsafe_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OidcSettings(client_secret=SecretStr("x"), **overrides)  # pyright: ignore[reportArgumentType]


def test_no_password_column_anywhere() -> None:
    """Authentication is fully delegated to the IdP: the schema stores no credentials."""
    pattern = re.compile(r"pass(word|wd)?|pwd|secret|credential", re.IGNORECASE)
    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if pattern.search(column.name)
    ]
    assert offenders == []
