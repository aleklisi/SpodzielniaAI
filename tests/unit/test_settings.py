from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from spoldzielnia_ai.core.settings import Settings, get_settings

if TYPE_CHECKING:
    from pathlib import Path

REQUIRED_ENV = {
    "SPOLDZIELNIA_DB__APP_PASSWORD": "env-app-pw",
    "SPOLDZIELNIA_REDIS__PASSWORD": "env-redis-pw",
    "SPOLDZIELNIA_OIDC__CLIENT_SECRET": "env-oidc-secret",
    "SPOLDZIELNIA_SECURITY__SESSION_SECRET": "env-session-secret",
}


def _load(secrets_dir: Path) -> Settings:
    return Settings(_env_file=None, _secrets_dir=secrets_dir)  # pyright: ignore[reportCallIssue]


def test_secrets_are_not_rendered_in_repr(settings: Settings) -> None:
    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    for secret in ("app-pw", "redis-pw", "oidc-secret", "session-secret"):
        assert secret not in rendered


def test_missing_required_values_fail_fast(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _load(tmp_path)

    missing = {".".join(str(p) for p in err["loc"]) for err in exc_info.value.errors()}
    assert {"db", "redis", "oidc", "security"} <= missing


def test_get_settings_fails_fast_without_config() -> None:
    with pytest.raises(ValidationError):
        get_settings()


def test_secrets_load_from_secrets_dir(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    files = {
        "db__app_password": "file-app-pw",
        "db__owner_password": "file-owner-pw",
        "redis__password": "file-redis-pw",
        "oidc__client_secret": "file-oidc-secret",
        "security__session_secret": "file-session-secret",
    }
    for name, value in files.items():
        (secrets_dir / name).write_text(value + "\n")

    loaded = _load(secrets_dir)

    assert loaded.db.app_password.get_secret_value() == "file-app-pw"
    assert loaded.db.owner_password is not None
    assert loaded.db.owner_password.get_secret_value() == "file-owner-pw"
    assert loaded.redis.password.get_secret_value() == "file-redis-pw"
    assert loaded.oidc.client_secret.get_secret_value() == "file-oidc-secret"
    assert loaded.security.session_secret.get_secret_value() == "file-session-secret"


def test_env_configures_non_secret_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("SPOLDZIELNIA_DB__HOST", "db.internal")
    monkeypatch.setenv("SPOLDZIELNIA_ENVIRONMENT", "dev")

    loaded = _load(tmp_path)

    assert loaded.db.host == "db.internal"
    assert loaded.environment == "dev"


def test_defaults_use_compose_service_names(settings: Settings) -> None:
    assert settings.db.host == "postgres"
    assert settings.redis.host == "redis"
    assert settings.qdrant.url.startswith("https://qdrant:")
    assert settings.s3.endpoint_url == "http://minio:9000"
    assert settings.llm.local_base_url == "http://llm:11434"
    assert settings.root_path == "/api"


def test_dsns_use_separate_roles(settings: Settings) -> None:
    assert settings.db.app_url().startswith("postgresql+asyncpg://spoldzielnia_app:app-pw@postgres")
    with pytest.raises(ValueError, match="owner_password"):
        settings.db.migration_url()


def test_redis_url_escapes_password(settings: Settings) -> None:
    from pydantic import SecretStr

    redis = settings.redis.model_copy(update={"password": SecretStr("p@ss/word:1")})
    assert redis.url() == "redis://:p%40ss%2Fword%3A1@redis:6379/0"
