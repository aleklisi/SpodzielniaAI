from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from spoldzielnia_ai.core.settings import (
    DbSettings,
    OidcSettings,
    RedisSettings,
    SecuritySettings,
    Settings,
    get_settings,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep tests independent of the developer's shell, ``.env`` and ``/run/secrets``."""
    import os

    for key in list(os.environ):
        if key.startswith("SPOLDZIELNIA_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        _secrets_dir=tmp_path / "no-secrets",  # pyright: ignore[reportCallIssue]
        environment="test",
        db=DbSettings(app_password=SecretStr("app-pw")),
        redis=RedisSettings(password=SecretStr("redis-pw")),
        oidc=OidcSettings(client_secret=SecretStr("oidc-secret")),
        security=SecuritySettings(
            session_secret=SecretStr("session-secret"),
            kek=SecretStr("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="),
        ),
    )
