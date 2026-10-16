"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel

from spoldzielnia_ai.core.logging import configure_logging, get_logger
from spoldzielnia_ai.core.readiness import ReadinessCheck, default_checks
from spoldzielnia_ai.core.settings import Settings, get_settings
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.migrate import head_revision
from spoldzielnia_ai.db.session import create_session_factory

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

    @app.middleware("http")
    async def security_headers(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app
