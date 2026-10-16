from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI reads it at runtime


async def test_rls_session_requires_authentication() -> None:
    """Fails closed before any database access: no session factory is even configured."""
    app = FastAPI()

    @app.get("/private")
    async def private(_session: RlsSessionDep) -> None:  # pyright: ignore[reportUnusedFunction]
        raise AssertionError("must not be reached")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/private")
    assert response.status_code == 401
