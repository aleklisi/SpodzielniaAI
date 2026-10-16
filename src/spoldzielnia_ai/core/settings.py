"""Application settings.

Non-secret configuration comes from environment variables (prefix ``SPOLDZIELNIA_``, nested
groups separated by ``__``, e.g. ``SPOLDZIELNIA_DB__HOST``). Secrets come from Docker secrets
mounted under ``/run/secrets``. Each file name is the nested field path, e.g.
``/run/secrets/db__app_password``. Defaults for hosts are compose service names and never
``localhost``.

Each process gets only the secrets it needs (least privilege): the API loads ``Settings``,
the one-shot ``migrate`` job loads ``MigrateSettings`` (owner DSN only), ``audit-verify`` loads
``AuditVerifySettings`` (app DSN only) and ``bootstrap`` loads ``BootstrapSettings`` (object
storage only).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    NestedSecretsSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy.engine import URL

DEFAULT_SECRETS_DIR = "/run/secrets"


class DbSettings(BaseModel):
    host: str = "postgres"
    port: int = 5432
    name: str = "spoldzielnia"
    app_user: str = "spoldzielnia_app"
    app_password: SecretStr | None = None
    owner_user: str = "spoldzielnia_owner"
    owner_password: SecretStr | None = None
    pool_size: int = Field(default=10, ge=1)

    def _url(self, user: str, password: SecretStr) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=user,
            password=password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)

    def app_url(self) -> str:
        """DSN for the runtime role (``NOBYPASSRLS``, not the table owner)."""
        if self.app_password is None:
            raise ValueError("db.app_password is required for the runtime DSN")
        return self._url(self.app_user, self.app_password)

    def migration_url(self) -> str:
        """DSN for the schema owner role, used only by Alembic."""
        if self.owner_password is None:
            raise ValueError("db.owner_password is required for migrations")
        return self._url(self.owner_user, self.owner_password)


class RedisSettings(BaseModel):
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: SecretStr

    def url(self) -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.host}:{self.port}/{self.db}"


# Asymmetric signatures only: `none` and HMAC (`HS*`, keyed with the shared client secret) are
# never acceptable for ID tokens.
OIDC_SIGNING_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256"})


class OidcSettings(BaseModel):
    """The single trusted issuer (Keycloak). External IdPs are brokered inside it."""

    provider: str = "keycloak"
    issuer: str = "https://auth.spoldzielnia.localhost/realms/spoldzielnia"
    client_id: str = "spoldzielnia-backend"
    client_secret: SecretStr
    redirect_uri: str = "https://spoldzielnia.localhost/api/auth/callback"
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    # Dotted path to the roles list in the ID token, e.g. "roles" or "realm_access.roles".
    roles_claim: str = "roles"
    # Dev: Caddy's local root CA. Prod: public certificates, no custom CA.
    ca_bundle: str | None = None
    signing_algs: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    leeway_seconds: int = Field(default=60, ge=0, le=300)
    login_state_ttl_seconds: int = Field(default=600, ge=60, le=600)
    http_timeout_seconds: float = Field(default=10.0, gt=0)
    metadata_ttl_seconds: int = Field(default=3600, ge=60)
    # A `kid` miss refetches the JWKS at most this often (unknown-kid tokens can't force a flood).
    jwks_min_refresh_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.signing_algs or not set(self.signing_algs) <= OIDC_SIGNING_ALGS:
            raise ValueError(f"oidc.signing_algs must be a subset of {sorted(OIDC_SIGNING_ALGS)}")
        if "openid" not in self.scopes:
            raise ValueError("oidc.scopes must include 'openid'")
        return self


class QdrantSettings(BaseModel):
    url: str = "https://qdrant:6333"
    api_key: SecretStr | None = None
    ca_bundle: str | None = None


class S3Settings(BaseModel):
    endpoint_url: str = "http://minio:9000"
    access_key: str = "spoldzielnia"
    secret_key: SecretStr | None = None
    bucket: str = "spoldzielnia-private"
    region: str = "us-east-1"


class LlmSettings(BaseModel):
    local_base_url: str = "http://llm:11434"
    local_model: str = "ollama/bielik"
    allow_cloud: bool = False


class SecuritySettings(BaseModel):
    session_secret: SecretStr
    allowed_origins: list[str] = Field(default_factory=lambda: ["https://spoldzielnia.localhost"])
    access_token_ttl_seconds: int = 15 * 60
    session_absolute_ttl_seconds: int = 12 * 60 * 60
    mfa_acr_values: list[str] = Field(default_factory=lambda: ["mfa"])


class _BaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPOLDZIELNIA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=DEFAULT_SECRETS_DIR,
        secrets_dir_missing="ok",  # local runs outside Docker have no /run/secrets
        secrets_prefix="",
        secrets_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["dev", "test", "prod"] = "prod"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Docker secrets map to nested fields: /run/secrets/db__app_password -> db.app_password.
        secrets = NestedSecretsSettingsSource(file_secret_settings)
        return (init_settings, env_settings, dotenv_settings, secrets)


class Settings(_BaseSettings):
    """Runtime settings of the API process."""

    root_path: str = "/api"
    api_host: str = "0.0.0.0"  # noqa: S104 - container listens on all interfaces behind Caddy
    api_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"

    db: DbSettings
    redis: RedisSettings
    oidc: OidcSettings
    security: SecuritySettings
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    llm: LlmSettings = Field(default_factory=LlmSettings)

    @model_validator(mode="after")
    def _require_runtime_secrets(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        return self


class MigrateSettings(_BaseSettings):
    """Settings of the one-shot ``migrate`` job: schema-owner DSN only."""

    db: DbSettings = Field(default_factory=DbSettings)

    @model_validator(mode="after")
    def _require_owner_password(self) -> Self:
        if self.db.owner_password is None:
            raise ValueError("db.owner_password is required")
        return self


class AuditVerifySettings(_BaseSettings):
    """Settings of the one-shot ``audit-verify`` job: runtime DSN only (it only reads the log)."""

    db: DbSettings = Field(default_factory=DbSettings)

    @model_validator(mode="after")
    def _require_app_password(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        return self


class BootstrapSettings(_BaseSettings):
    """Settings of the one-shot ``bootstrap`` job: object storage (and Qdrant from 4.1)."""

    s3: S3Settings = Field(default_factory=S3Settings)

    @model_validator(mode="after")
    def _require_s3_secret(self) -> Self:
        if self.s3.secret_key is None:
            raise ValueError("s3.secret_key is required")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process; raises ``ValidationError`` if required values are missing."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env/secrets
