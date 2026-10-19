"""Application settings.

Non-secret configuration comes from environment variables (prefix ``SPOLDZIELNIA_``, nested
groups separated by ``__``, e.g. ``SPOLDZIELNIA_DB__HOST``). Secrets come from Docker secrets
mounted under ``/run/secrets``. Each file name is the nested field path, e.g.
``/run/secrets/db__app_password``. Defaults for hosts are compose service names and never
``localhost``.

Each process gets only the secrets it needs (least privilege): the API loads ``Settings``,
the one-shot ``migrate`` job loads ``MigrateSettings`` (owner DSN only), ``audit-verify`` loads
``AuditVerifySettings`` (app DSN only) and ``bootstrap`` loads ``BootstrapSettings`` (object
storage and Qdrant only).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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

    # Celery broker (2.5), kept apart from sessions/login state in `db`.
    broker_db: int = 1

    def url(self, db: int | None = None) -> str:
        password = quote(self.password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.host}:{self.port}/{self.db if db is None else db}"

    def broker_url(self) -> str:
        return self.url(self.broker_db)


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
    post_logout_redirect_uri: str = "https://spoldzielnia.localhost/"
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
    # Internal CA (compose secret `internal_ca`): Qdrant serves TLS with a dev/internal cert.
    ca_bundle: str | None = None
    # Dimension of `llm.embed_model`. Bootstrap refuses an existing collection of another size,
    # and the embedder refuses vectors of another size (4.1).
    vector_size: int = Field(default=1024, ge=8, le=8192)
    # Bounded wait for synchronous index operations (consent propagation, 4.3).
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class S3Settings(BaseModel):
    endpoint_url: str = "http://minio:9000"
    access_key: str = "spoldzielnia"
    secret_key: SecretStr | None = None
    bucket: str = "spoldzielnia-private"
    region: str = "us-east-1"


class ClamavSettings(BaseModel):
    host: str = "clamav"
    port: int = 3310
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    scan_timeout_seconds: float = Field(default=180.0, gt=0)
    # Must not exceed clamd's StreamMaxLength (compose: CLAMD_CONF_StreamMaxLength=210M).
    stream_max_bytes: int = Field(default=210 * 1024 * 1024, gt=0)


class UploadSettings(BaseModel):
    # Spool directory (tmpfs in the container). None: the system temp dir.
    tmp_dir: str | None = None
    # Each upload needs up to ~2x its size in the spool: bound the parallelism.
    max_concurrent: int = Field(default=2, ge=1)
    # Hard cap on the streamed file part, before type-specific limits apply.
    max_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    max_image_pixels: int = Field(default=60_000_000, gt=0)


class FetchSettings(BaseModel):
    """SSRF-safe link fetching (egress worker)."""

    allow_http: bool = False
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443, 8080, 8443])
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    total_timeout_seconds: float = Field(default=15.0, gt=0)
    max_redirects: int = Field(default=3, ge=0, le=10)
    max_body_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    max_url_length: int = 2048
    allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "application/xhtml+xml",
            "text/plain",
            "text/markdown",
            "application/pdf",
        ]
    )
    user_agent: str = "SpoldzielniaAI-LinkFetcher/1.0 (+https://spoldzielnia.localhost)"


class ModelSettings(BaseModel):
    """Local ML models in the read-only `models` volume (filled by `make models`)."""

    root: str = "/models"
    whisper_model: str = "small"  # dev: small (CPU int8). Prod: large-v3 (GPU).
    whisper_device: Literal["cpu", "cuda", "auto"] = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "pl"
    whisper_beam_size: int = Field(default=5, ge=1)
    docling_ocr: bool = True

    @property
    def whisper_dir(self) -> str:
        return f"{self.root}/whisper/{self.whisper_model}"

    @property
    def docling_dir(self) -> str:
        return f"{self.root}/docling"

    @property
    def injection_dir(self) -> str:
        return f"{self.root}/injection/deberta-v3-base-prompt-injection-v2"


class WorkerSecuritySettings(BaseModel):
    """Workers decrypt and encrypt private-zone data: they need the KEK and nothing else."""

    kek: SecretStr


class PromotionSettings(BaseModel):
    daily_limit: int = Field(default=20, ge=1)  # promotions per user per UTC day


class GovernanceSettings(BaseModel):
    """Expert review and creator consent (Stage 4)."""

    # Distinct experts whose approval makes an article CANONICAL.
    review_quorum: int = Field(default=1, ge=1, le=10)
    # Revoking contributions needs a login at most this old (step-up otherwise).
    revoke_max_auth_age_seconds: int = Field(default=600, ge=60, le=3600)


class LlmSettings(BaseModel):
    """LLM endpoints over the standard OpenAI API (LM Studio, Ollama, vLLM, …).

    The local endpoint is the compose service ``llm``. In dev it relays to the host (LM Studio
    or Ollama), in prod it is an Ollama container. A model left empty means "not available":
    purposes that must stay local then fail with ``LocalModelUnavailable``, never fall back to
    the cloud.
    """

    local_base_url: str = "http://llm:11434/v1"
    chat_model: str | None = None  # e.g. "speakleash/bielik-11b-v2.3-instruct"
    vlm_model: str | None = None  # e.g. "qwen2.5-vl-7b-instruct"
    embed_model: str | None = None  # e.g. "text-embedding-embeddinggemma"
    # Docker secret `llm_api_key` (LM Studio requires a token; Ollama ignores it).
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=180.0, gt=0)
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    # How a JSON schema reaches the model: ``response_format`` (constrained decoding) or, for
    # servers/models that can't do that, the system prompt (output is validated either way).
    structured_output: Literal["json_schema", "prompt"] = "json_schema"
    # Optional cloud deployment, only for purposes that may leave the server (none in Stage 3).
    allow_cloud: bool = False
    cloud_base_url: str | None = None
    cloud_model: str | None = None
    cloud_api_key: SecretStr | None = None

    @field_validator("chat_model", "vlm_model", "embed_model", "cloud_model", "cloud_base_url")
    @classmethod
    def _empty_is_unset(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class SecuritySettings(BaseModel):
    # Keys for token encryption at rest and CSRF tokens are derived from it (HKDF).
    session_secret: SecretStr
    # Key-encryption key of the private zone (base64, 32 bytes): Docker secret `kek`.
    kek: SecretStr
    allowed_origins: list[str] = Field(default_factory=lambda: ["https://spoldzielnia.localhost"])
    # How long the server trusts a session before re-checking with the IdP (refresh).
    access_token_ttl_seconds: int = Field(default=15 * 60, ge=60)
    session_absolute_ttl_seconds: int = Field(default=12 * 60 * 60, ge=300)
    mfa_acr_values: list[str] = Field(default_factory=lambda: ["mfa"])
    mfa_amr_values: list[str] = Field(default_factory=lambda: ["otp", "mfa", "hwk", "fido"])
    # Waiting for another request's refresh of the same token family.
    refresh_lock_timeout_seconds: float = Field(default=10.0, gt=0)


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
    clamav: ClamavSettings = Field(default_factory=ClamavSettings)
    uploads: UploadSettings = Field(default_factory=UploadSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    promotion: PromotionSettings = Field(default_factory=PromotionSettings)
    governance: GovernanceSettings = Field(default_factory=GovernanceSettings)

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


class WorkerSettings(_BaseSettings):
    """Celery workers (`spoldzielnia worker`): app DSN, broker, KEK, storage, scanner, fetcher,
    models. No session secret and no OIDC client secret."""

    db: DbSettings = Field(default_factory=DbSettings)
    redis: RedisSettings
    security: WorkerSecuritySettings
    s3: S3Settings = Field(default_factory=S3Settings)
    clamav: ClamavSettings = Field(default_factory=ClamavSettings)
    uploads: UploadSettings = Field(default_factory=UploadSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    concurrency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _require_app_password(self) -> Self:
        if self.db.app_password is None:
            raise ValueError("db.app_password is required")
        return self


class BootstrapSettings(_BaseSettings):
    """Settings of the one-shot ``bootstrap`` job: object storage and Qdrant collections."""

    s3: S3Settings = Field(default_factory=S3Settings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)

    @model_validator(mode="after")
    def _require_secrets(self) -> Self:
        if self.s3.secret_key is None:
            raise ValueError("s3.secret_key is required")
        if self.qdrant.api_key is None:
            raise ValueError("qdrant.api_key is required")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process; raises ``ValidationError`` if required values are missing."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env/secrets
