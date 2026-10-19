"""Chunk 6.1: security headers on API responses, and the OpenAPI schema the frontend uses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from spoldzielnia_ai.cli import main
from spoldzielnia_ai.core.headers import API_CSP, SECURITY_HEADERS
from spoldzielnia_ai.main import create_app

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings

REPO = Path(__file__).resolve().parents[2]


def _client(settings: Settings) -> AsyncClient:
    app = create_app(settings, readiness_checks={})
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://spoldzielnia.localhost")


@pytest.mark.parametrize("path", ["/healthz", "/me", "/no-such-route"])
async def test_every_api_response_carries_the_security_headers(
    settings: Settings, path: str
) -> None:
    async with _client(settings) as client:
        response = await client.get(path)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["Content-Security-Policy"] == API_CSP


async def test_csrf_refusals_carry_them_too(settings: Settings) -> None:
    async with _client(settings) as client:
        response = await client.post("/rag/ask", json={"question": "x"})
    assert response.status_code == 403
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == API_CSP


async def test_dev_swagger_ui_is_exempt_from_the_api_csp(settings: Settings) -> None:
    async with _client(settings) as client:  # environment "test": docs enabled
        docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "Content-Security-Policy" not in docs.headers
    assert docs.headers["X-Frame-Options"] == "DENY"


def test_committed_openapi_schema_is_current(tmp_path: Path) -> None:
    """frontend/openapi.json (and the generated TS types) must match the API: run
    `npm run gen:api` in frontend/ after changing a route or schema."""
    out = tmp_path / "openapi.json"
    assert main(["openapi", "--out", str(out)]) == 0
    committed = json.loads((REPO / "frontend" / "openapi.json").read_text())
    assert json.loads(out.read_text()) == committed
