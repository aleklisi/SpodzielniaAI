from __future__ import annotations

from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

from spoldzielnia_ai.db.migrate import head_revision
from spoldzielnia_ai.main import create_app

if TYPE_CHECKING:
    from spoldzielnia_ai.core.readiness import ReadinessCheck
    from spoldzielnia_ai.core.settings import Settings


def _static(result: bool) -> ReadinessCheck:
    async def check() -> bool:
        return result

    return check


def _client(settings: Settings, checks: dict[str, ReadinessCheck]) -> AsyncClient:
    app = create_app(settings, readiness_checks=checks)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_healthz_returns_ok(settings: Settings) -> None:
    async with _client(settings, {}) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_readyz_ok_when_all_checks_pass(settings: Settings) -> None:
    async with _client(settings, {"postgres": _static(True), "redis": _static(True)}) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": True, "redis": True},
        "schema_revision": head_revision(),
    }


async def test_readyz_503_when_a_dependency_is_down(settings: Settings) -> None:
    async with _client(settings, {"postgres": _static(True), "redis": _static(False)}) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"]["redis"] is False


async def test_root_path_is_applied(settings: Settings) -> None:
    app = create_app(settings, readiness_checks={})
    assert app.root_path == "/api"


async def test_openapi_hidden_in_prod(settings: Settings) -> None:
    prod = settings.model_copy(update={"environment": "prod"})
    async with _client(prod, {}) as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404
