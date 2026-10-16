from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from spoldzielnia_ai.core.settings import (
    AuditVerifySettings,
    BootstrapSettings,
    MigrateSettings,
    Settings,
    get_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

REQUIRED_ENV = {
    "SPOLDZIELNIA_DB__APP_PASSWORD": "env-app-pw",
    "SPOLDZIELNIA_REDIS__PASSWORD": "env-redis-pw",
    "SPOLDZIELNIA_OIDC__CLIENT_SECRET": "env-oidc-secret",
    "SPOLDZIELNIA_SECURITY__SESSION_SECRET": "env-session-secret",
    "SPOLDZIELNIA_SECURITY__KEK": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
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
        "security__kek": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    }
    for name, value in files.items():
        (secrets_dir / name).write_text(value + "\n")

    loaded = _load(secrets_dir)

    assert loaded.db.app_password is not None
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
    assert settings.llm.local_base_url == "http://llm:11434/v1"  # OpenAI-compatible API
    assert settings.root_path == "/api"


def test_dsns_use_separate_roles(settings: Settings) -> None:
    assert settings.db.app_url().startswith("postgresql+asyncpg://spoldzielnia_app:app-pw@postgres")
    with pytest.raises(ValueError, match="owner_password"):
        settings.db.migration_url()


def test_redis_url_escapes_password(settings: Settings) -> None:
    from pydantic import SecretStr

    redis = settings.redis.model_copy(update={"password": SecretStr("p@ss/word:1")})
    assert redis.url() == "redis://:p%40ss%2Fword%3A1@redis:6379/0"


def test_api_settings_require_app_password(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("SPOLDZIELNIA_DB__APP_PASSWORD")
    monkeypatch.setenv("SPOLDZIELNIA_DB__HOST", "postgres")

    with pytest.raises(ValidationError, match="app_password"):
        _load(tmp_path)


def test_migrate_settings_need_only_owner_password(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "db__owner_password").write_text("owner-pw")

    loaded = MigrateSettings(_env_file=None, _secrets_dir=secrets_dir)  # pyright: ignore[reportCallIssue]

    assert loaded.db.migration_url().startswith(
        "postgresql+asyncpg://spoldzielnia_owner:owner-pw@postgres:5432/"
    )
    with pytest.raises(ValueError, match="app_password"):
        loaded.db.app_url()


def test_migrate_settings_fail_without_owner_password(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="owner_password"):
        MigrateSettings(_env_file=None, _secrets_dir=tmp_path)  # pyright: ignore[reportCallIssue]


def test_bootstrap_settings_need_only_s3_secret(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "s3__secret_key").write_text("minio-pw")

    loaded = BootstrapSettings(_env_file=None, _secrets_dir=secrets_dir)  # pyright: ignore[reportCallIssue]

    assert loaded.s3.secret_key is not None
    assert loaded.s3.secret_key.get_secret_value() == "minio-pw"
    with pytest.raises(ValidationError, match="secret_key"):
        BootstrapSettings(_env_file=None, _secrets_dir=tmp_path)  # pyright: ignore[reportCallIssue]


def test_audit_verify_settings_need_only_app_password(tmp_path: Path) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "db__app_password").write_text("app-pw")

    loaded = AuditVerifySettings(_env_file=None, _secrets_dir=secrets_dir)  # pyright: ignore[reportCallIssue]

    assert loaded.db.app_url().startswith("postgresql+asyncpg://spoldzielnia_app:app-pw@postgres")
    with pytest.raises(ValueError, match="owner_password"):
        loaded.db.migration_url()
    with pytest.raises(ValidationError, match="app_password"):
        AuditVerifySettings(_env_file=None, _secrets_dir=tmp_path)  # pyright: ignore[reportCallIssue]
