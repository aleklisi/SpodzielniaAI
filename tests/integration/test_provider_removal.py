"""Chunk 11.1 (US-05): removing a model provider. Deleted when no activated revision used it,
archived when history names it; refused while something running needs it. The service rules
(R1), the database guards of migration 0026, and the ``DELETE /admin/llm/providers/{id}`` API."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import IndexGenerationStatus, LlmCapability, LlmRoutePurpose
from spoldzielnia_ai.db.models import (
    IndexGeneration,
    LlmConfigRevision,
    LlmModel,
    LlmProvider,
    LlmRouting,
    SecurityAuditLog,
)
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.config_service import (
    ClearanceIn,
    ConfigError,
    LlmConfigService,
    ModelIn,
    ProviderIn,
)
from spoldzielnia_ai.llm.config_store import resolve
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot
from tests.fixtures.app import harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.sql import Executable

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

ENV = LlmSettings(
    chat_model="bielik-11b",
    embed_model="gemma-embed",
    api_key=SecretStr("lm-studio-token-0001"),
    local_endpoints=["http://vllm:8000"],
    cloud_hosts=["api.example.com"],
    allow_cloud=True,
    private_data_endpoints=["http://llm:11434", "http://vllm:8000", "https://api.example.com"],
)
CLEARANCE = ClearanceIn(region="UE (Francja)", retention="30 dni", contract_ref="DPA 2026/01")
KEY = "sk-remove-me-0000-WXYZ"


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def service(factory: SessionFactory) -> LlmConfigService:
    cipher = EnvelopeCipher(StaticKeyProvider(base64.b64encode(os.urandom(32)).decode()))
    service = LlmConfigService(factory, cipher, ENV)
    await service.ensure_seeded(None)
    return service


async def _write(factory: SessionFactory, statement: Executable) -> None:
    """One statement in its own transaction, past the service (as a compromised app would)."""
    async with factory() as session, session.begin():
        await session.execute(statement)


async def _cloud(service: LlmConfigService, name: str = "cloud") -> tuple[LlmProvider, LlmModel]:
    provider = await service.create_provider(
        None,
        ProviderIn(name=name, base_url="https://api.example.com/v1", api_key=SecretStr(KEY)),
    )
    model = await service.create_model(
        None, ModelIn(provider_id=provider.id, model_name="gpt-x", capability=LlmCapability.CHAT)
    )
    await service.record_probe(None, model.id, True, {"checks": []})
    return provider, model


async def _activate(service: LlmConfigService, routes: dict[LlmRoutePurpose, Any]) -> int:
    draft = await service.create_draft(None, None, None)
    await service.set_routing(None, draft.revision.number, routes)
    return (await service.activate(None, draft.revision.number)).revision.number


async def _seeded_rag(service: LlmConfigService) -> Any:
    active = await service.active()
    assert active is not None
    return active.routing[LlmRoutePurpose.RAG]


async def _used_once(service: LlmConfigService) -> tuple[LlmProvider, LlmModel, int]:
    """A cleared cloud provider that served RAG in one revision, then was replaced."""
    provider, model = await _cloud(service)
    await service.clear_provider(None, provider.id, CLEARANCE)
    local = await _seeded_rag(service)
    await service.record_probe(None, local, True, {"checks": []})
    used = await _activate(service, {LlmRoutePurpose.RAG: model.id})
    await _activate(service, {LlmRoutePurpose.RAG: local})
    return provider, model, used


async def _audit(factory: SessionFactory) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = await session.scalars(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.LLM_PROVIDER_DELETED.value
            )
        )
        return [dict(r.payload) for r in rows]


# --- service ------------------------------------------------------------------------------------


async def test_an_unused_provider_is_deleted_with_its_models(
    service: LlmConfigService, factory: SessionFactory
) -> None:
    provider, model = await _cloud(service)
    removal = (await service.removals())[provider.id]
    assert (removal.blocked, removal.archive) == (None, False)

    assert await service.delete_provider(None, provider.id) == "deleted"

    async with factory() as session:
        assert await session.get(LlmProvider, provider.id) is None
        assert await session.get(LlmModel, model.id) is None
    assert provider.id not in {p.id for p in await service.providers()}
    (payload,) = await _audit(factory)
    assert payload == {
        "name": "cloud",
        "kind": "CLOUD",
        "base_url": "https://api.example.com/v1",
        "mode": "deleted",
        "models": 1,
        "was_cleared": False,
    }
    assert KEY not in str(payload)
    with pytest.raises(ConfigError) as exc:
        await service.delete_provider(None, provider.id)
    assert (exc.value.code, exc.value.status) == ("provider_not_found", 404)


async def test_a_provider_the_running_configuration_needs_is_not_removed(
    service: LlmConfigService, factory: SessionFactory
) -> None:
    (seeded,) = await service.providers()
    assert (await service.removals())[seeded.id].blocked == "provider_in_use"
    with pytest.raises(ConfigError) as exc:
        await service.delete_provider(None, seeded.id)
    assert (exc.value.code, exc.value.status) == ("provider_in_use", 409)

    # An open draft that routes to it.
    provider, model = await _cloud(service)
    draft = await service.create_draft(None, None, None)
    await service.set_routing(None, draft.revision.number, {LlmRoutePurpose.RAG: model.id})
    with pytest.raises(ConfigError) as exc:
        await service.delete_provider(None, provider.id)
    assert (exc.value.code, exc.value.detail) == ("provider_in_draft", {"draft": 2})
    await service.delete_draft(None, draft.revision.number)

    # A serving or building index generation built by one of its models.
    deployment = {"provider_id": str(provider.id), "model_id": str(model.id)}
    await _write(
        factory,
        insert(IndexGeneration).values(
            id=uuid7(),
            status=IndexGenerationStatus.BUILDING,
            embed_key="k",
            model="cloud/gpt-x",
            deployment=deployment,
            revision=1,
            dimension=8,
            private_collection="p",
            canonical_collection="c",
        ),
    )
    with pytest.raises(ConfigError) as exc:
        await service.delete_provider(None, provider.id)
    assert exc.value.code == "provider_in_index"
    await _write(
        factory,
        update(IndexGeneration).values(
            status=IndexGenerationStatus.CANCELLED, retired_at=datetime.now(UTC)
        ),
    )
    assert await service.delete_provider(None, provider.id) == "deleted"


async def test_a_provider_history_names_is_archived(
    service: LlmConfigService, factory: SessionFactory
) -> None:
    provider, model, used = await _used_once(service)
    assert (await service.removals())[provider.id].archive is True

    assert await service.delete_provider(None, provider.id) == "archived"

    assert provider.id not in {p.id for p in await service.providers()}
    assert model.id not in {m.id for m in await service.models()}
    async with factory() as session:
        row = await session.get(LlmProvider, provider.id)
        stored = await session.get(LlmModel, model.id)
    assert row is not None
    assert stored is not None
    assert row.deleted_at is not None
    assert row.disabled_at is not None
    assert (row.api_key_ciphertext, row.api_key_wrapped, row.api_key_hint) == (None, None, None)
    assert row.private_data_cleared is False
    assert stored.disabled_at is not None
    (payload,) = await _audit(factory)
    assert (payload["mode"], payload["was_cleared"]) == ("archived", True)

    # History still shows the revision by name, but it cannot come back.
    view = await service.revision(used)
    snapshot = ConfigSnapshot.model_validate(view.revision.snapshot)
    assert snapshot.deployment_for(LlmRoutePurpose.RAG).display_name == "cloud/gpt-x"  # pyright: ignore[reportOptionalMemberAccess]
    with pytest.raises(ConfigError) as exc:
        await service.rollback(None, used, None)
    assert (exc.value.code, exc.value.detail) == ("provider_removed", {"purpose": "RAG"})

    # Gone for every other admin action, and its name is free again.
    for call in (
        service.provider(provider.id),
        service.clear_provider(None, provider.id, CLEARANCE),
        service.set_provider_disabled(None, provider.id, False),
        service.set_model_disabled(None, model.id, False),
        service.record_probe(None, model.id, True, {}),
    ):
        with pytest.raises(ConfigError) as exc:
            await call
        assert exc.value.status == 404
    again, _ = await _cloud(service)
    assert again.id != provider.id


async def test_an_archived_provider_never_resolves(
    service: LlmConfigService, factory: SessionFactory
) -> None:
    provider, _, used = await _used_once(service)
    await service.delete_provider(None, provider.id)
    async with factory() as session:
        row = await session.get(LlmProvider, provider.id)
    assert row is not None
    view = await service.revision(used)
    snapshot = ConfigSnapshot.model_validate(view.revision.snapshot)
    config = resolve(used, snapshot, {provider.id: row}, service._cipher, ENV)  # pyright: ignore[reportPrivateUsage]
    assert config.routes == {}
    assert config.deployments == {}


# --- database guards (migration 0026) -----------------------------------------------------------


async def test_the_database_keeps_an_archived_provider_out_of_use(
    service: LlmConfigService, factory: SessionFactory
) -> None:
    provider, model, used = await _used_once(service)
    await service.delete_provider(None, provider.id)
    where = LlmProvider.id == provider.id
    for statement in (
        update(LlmProvider).where(where).values(deleted_at=None),
        update(LlmProvider).where(where).values(deleted_at=datetime.now(UTC)),
        update(LlmProvider)
        .where(where)
        .values(
            private_data_cleared=True,
            cleared_region="UE",
            cleared_retention="0",
            cleared_contract_ref="x",
            cleared_at=datetime.now(UTC),
        ),
    ):
        with pytest.raises(DBAPIError):
            await _write(factory, statement)

    draft = await service.create_draft(None, None, None)
    async with factory() as session:
        draft_id = await session.scalar(
            select(LlmConfigRevision.id).where(LlmConfigRevision.number == draft.revision.number)
        )
    with pytest.raises(DBAPIError):
        await _write(
            factory,
            update(LlmRouting)
            .where(LlmRouting.revision_id == draft_id, LlmRouting.purpose == LlmRoutePurpose.RAG)
            .values(model_id=model.id),
        )
    old = (await service.revision(used)).revision.snapshot
    with pytest.raises(DBAPIError):
        await _write(
            factory,
            update(LlmConfigRevision)
            .where(LlmConfigRevision.id == draft_id)
            .values(status="ACTIVE", snapshot=old),
        )


async def test_the_name_of_an_archived_provider_is_free(factory: SessionFactory) -> None:
    async with factory() as session, session.begin():
        session.add(
            LlmProvider(
                name="gone",
                kind="LOCAL",
                base_url="http://vllm:8000/v1",
                deleted_at=datetime.now(UTC),
            )
        )
        session.add(LlmProvider(name="gone", kind="LOCAL", base_url="http://vllm:8000/v1"))
    with pytest.raises(DBAPIError):
        await _write(
            factory,
            insert(LlmProvider).values(
                id=uuid7(), name="gone", kind="LOCAL", base_url="http://vllm:8000/v1"
            ),
        )


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    async with harness(settings.model_copy(update={"llm": ENV}), factory) as h:
        yield h


async def _admin(h: Harness) -> LoggedIn:
    return await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])


async def test_admin_removes_a_provider_and_gets_the_refreshed_list(h: Harness) -> None:
    admin = await _admin(h)
    created = await admin.client.post(
        "/admin/llm/providers",
        json={"name": "stary-test", "base_url": "http://vllm:8000/v1"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    listed = {p["name"]: p for p in (await admin.client.get("/admin/llm/providers")).json()}
    assert listed["local"]["removal_blocked"] == "provider_in_use"
    assert (listed["stary-test"]["removal_blocked"], listed["stary-test"]["removal_archives"]) == (
        None,
        False,
    )

    member = await h.login()
    path = f"/admin/llm/providers/{created.json()['id']}"
    assert (await member.client.delete(path, headers=member.headers)).status_code == 403
    assert (await admin.client.delete(path)).status_code == 403  # no CSRF token

    removed = await admin.client.delete(path, headers=admin.headers)
    assert removed.status_code == 204, removed.text
    names = [p["name"] for p in (await admin.client.get("/admin/llm/providers")).json()]
    assert names == ["local"]
    assert (await admin.client.delete(path, headers=admin.headers)).status_code == 404

    local = listed["local"]["id"]
    refused = await admin.client.delete(f"/admin/llm/providers/{local}", headers=admin.headers)
    assert refused.status_code == 409
    assert refused.json()["detail"] == {"error": "provider_in_use"}
