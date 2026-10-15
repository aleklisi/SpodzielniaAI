from __future__ import annotations

import json

import pytest
import structlog

from spoldzielnia_ai.core.logging import (
    REDACTED,
    build_processors,
    is_sensitive_key,
    redact_sensitive,
)


@pytest.mark.parametrize(
    "key",
    [
        "authorization",
        "Authorization",
        "cookie",
        "Set-Cookie",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "db_password",
        "content",
        "client_secret",
        "X-CSRF-Token",
    ],
)
def test_sensitive_keys_detected(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["event", "user_id", "content_type", "content_hash", "path"])
def test_non_sensitive_keys_kept(key: str) -> None:
    assert not is_sensitive_key(key)


def test_redacts_top_level_and_nested_values() -> None:
    event = {
        "event": "http.request",
        "user_id": "u-1",
        "authorization": "Bearer abc",
        "headers": {"Cookie": "__Host-sid=xyz", "Accept": "text/html"},
        "items": [{"password": "hunter2", "name": "x"}],
        "content": "private note body",
    }

    out = redact_sensitive(None, "info", event)

    assert out["event"] == "http.request"
    assert out["user_id"] == "u-1"
    assert out["authorization"] == REDACTED
    assert out["headers"] == {"Cookie": REDACTED, "Accept": "text/html"}
    assert out["items"] == [{"password": REDACTED, "name": "x"}]
    assert out["content"] == REDACTED


def test_rendered_json_never_contains_secret(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.configure(
        processors=build_processors(json=True),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        structlog.get_logger().info("login", token="super-secret-token", user="member1")  # noqa: S106
    finally:
        structlog.reset_defaults()

    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert "super-secret-token" not in line
    assert payload["token"] == REDACTED
    assert payload["user"] == "member1"
    assert payload["level"] == "info"
