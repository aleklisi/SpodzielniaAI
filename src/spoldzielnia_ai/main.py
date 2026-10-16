"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis

from spoldzielnia_ai.api.attachments import router as attachments_router
from spoldzielnia_ai.api.auth import router as auth_router
from spoldzielnia_ai.api.links import router as links_router
from spoldzielnia_ai.api.me import router as me_router
from spoldzielnia_ai.api.notes import router as notes_router
from spoldzielnia_ai.auth.crypto import CsrfTokens, TokenSealer, derive_key
from spoldzielnia_ai.auth.csrf import csrf_middleware
from spoldzielnia_ai.auth.deps import DbAuditSink
from spoldzielnia_ai.auth.oidc_client import OidcClient
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.logging import configure_logging, get_logger
from spoldzielnia_ai.core.readiness import ReadinessCheck, default_checks
from spoldzielnia_ai.core.settings import Settings, get_settings
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.migrate import head_revision
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.files.antivirus import ClamdScanner
from spoldzielnia_ai.files.storage import S3ObjectStorage
from spoldzielnia_ai.notebook.uploads import NoProcessing

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

log = get_logger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, bool]
    schema_revision: str


def create_app(
    settings: Settings | None = None,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.environment != "dev")
    engine = create_app_engine(settings.db)
    # Neither client connects until first use.
    redis: Redis = Redis.from_url(settings.redis.url())  # pyright: ignore[reportUnknownMemberType]
    oidc_client = OidcClient(settings.oidc)
    schema_revision = head_revision()
    checks = (
        readiness_checks
        if readiness_checks is not None
        else default_checks(settings, engine, schema_revision)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        log.info("app.startup", environment=settings.environment, schema_revision=schema_revision)
        yield
        await oidc_client.aclose()
        await redis.aclose()
        await engine.dispose()
        log.info("app.shutdown")

    is_prod = settings.environment == "prod"
    app = FastAPI(
        title="Spółdzielnia AI",
        root_path=settings.root_path,
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis
    app.state.oidc_client = oidc_client
    session_secret = settings.security.session_secret.get_secret_value()
    app.state.token_sealer = TokenSealer(derive_key(session_secret, "redis-tokens"))
    csrf_tokens = CsrfTokens(derive_key(session_secret, "csrf"))
    app.state.csrf_tokens = csrf_tokens
    app.state.audit_sink = DbAuditSink(app.state)
    app.state.cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    app.state.storage = S3ObjectStorage(settings.s3)
    app.state.scanner = ClamdScanner(settings.clamav)
    app.state.processing_queue = NoProcessing()  # Celery from 2.5
    app.state.upload_slots = asyncio.Semaphore(settings.uploads.max_concurrent)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(notes_router)
    app.include_router(attachments_router)
    app.include_router(links_router)

    @app.get("/healthz", response_model=HealthResponse, tags=["ops"])
    async def healthz() -> HealthResponse:  # pyright: ignore[reportUnusedFunction]
        """Liveness: the process is up. Does not touch dependencies."""
        return HealthResponse()

    @app.get("/readyz", response_model=ReadinessResponse, tags=["ops"])
    async def readyz(response: Response) -> ReadinessResponse:  # pyright: ignore[reportUnusedFunction]
        """Readiness: all backing services required to serve traffic are reachable."""
        results = {name: await check() for name, check in checks.items()}
        ok = all(results.values())
        if not ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="ok" if ok else "unavailable", checks=results, schema_revision=schema_revision
        )

    # Registered before `security_headers`, so its 403s still get the security headers.
    app.middleware("http")(csrf_middleware(settings.security.allowed_origins, csrf_tokens))

    @app.middleware("http")
    async def security_headers(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app
