"""The real app wired for integration tests: Postgres (testcontainer), fakeredis, fake IdP,
in-memory object storage. ``Harness.client(subject)`` returns a logged-in HTTP client."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import respx
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient

from spoldzielnia_ai.files.storage import MemoryObjectStorage
from spoldzielnia_ai.main import create_app
from spoldzielnia_ai.notebook.uploads import NoProcessing
from tests.fixtures.oidc import FakeIdP

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory

SITE = "https://spoldzielnia.localhost"
HOST = "spoldzielnia.localhost"


@dataclass
class LoggedIn:
    client: AsyncClient
    headers: dict[str, str]  # Origin + X-CSRF-Token for unsafe requests
    user_id: str = ""


@dataclass
class Harness:
    app: FastAPI
    idp: FakeIdP
    storage: MemoryObjectStorage
    router: respx.MockRouter
    clients: list[AsyncClient] = field(default_factory=list[AsyncClient])

    async def login(self, subject: str | None = None, **claims: Any) -> LoggedIn:
        client = AsyncClient(transport=ASGITransport(app=self.app), base_url=SITE)
        self.clients.append(client)
        start = await client.get("/auth/login")
        params = parse_qs(urlsplit(start.headers["location"]).query)
        self.idp.id_token = self.idp.make_id_token(
            nonce=params["nonce"][0], sub=subject or secrets.token_hex(8), **claims
        )
        callback = await client.get(
            "/auth/callback", params={"state": params["state"][0], "code": "c"}
        )
        assert callback.status_code == 303, callback.text
        csrf = client.cookies.get("__Host-csrf", domain=HOST) or ""
        me = await client.get("/me")
        return LoggedIn(client, {"Origin": SITE, "X-CSRF-Token": csrf}, me.json()["id"])


@asynccontextmanager
async def harness(settings: Settings, factory: SessionFactory) -> AsyncGenerator[Harness]:
    app = create_app(settings, readiness_checks={})
    app.state.redis = FakeAsyncRedis()
    app.state.session_factory = factory
    storage = MemoryObjectStorage()
    app.state.storage = storage
    app.state.processing_queue = NoProcessing()  # tests opt in to Celery (eager) explicitly
    idp = FakeIdP()
    with respx.mock(assert_all_called=False) as router:
        idp.mount(router)
        h = Harness(app, idp, storage, router)
        try:
            yield h
        finally:
            for client in h.clients:
                await client.aclose()
