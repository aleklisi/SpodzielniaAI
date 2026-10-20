"""Chunk 7.1: LLM configuration in Postgres: seeding, encrypted keys, revisions, and the
database layer of the local-only rule (triggers of migration 0012)."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import SecretStr
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import (
    LlmCapability,
    LlmProviderKind,
    LlmRevisionStatus,
    LlmRoutePurpose,
)
from spoldzielnia_ai.db.models import (
    LlmConfigRevision,
    LlmModel,
    LlmProvider,
    LlmRouting,
    SecurityAuditLog,
)
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.config_service import (
    ConfigError,
    LlmConfigService,
    ModelIn,
    ProviderIn,
    ProviderPatch,
)
from spoldzielnia_ai.llm.snapshot import ConfigSnapshot

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from sqlalchemy.sql import Executable

pytestmark = pytest.mark.integration

ENV = LlmSettings(
    chat_model="bielik-11b",
    vlm_model="qwen-vl",
    embed_model="gemma-embed",
    api_key=SecretStr("lm-studio-token-0001"),
    local_endpoints=["http://vllm:8000"],
    cloud_hosts=["api.example.com"],
)
DIM = 32


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(StaticKeyProvider(base64.b64encode(os.urandom(32)).decode()))


def _service(factory: SessionFactory, cipher: EnvelopeCipher, **overrides: Any) -> LlmConfigService:
    settings = ENV.model_copy(update=overrides)
    return LlmConfigService(factory, cipher, settings, vector_size=DIM)


async def _write(factory: SessionFactory, statement: Executable) -> None:
    """One statement in its own transaction, past the service (as a compromised app would)."""
    async with factory() as session, session.begin():
        await session.execute(statement)


async def _probed(service: LlmConfigService, model: LlmModel, ok: bool = True) -> None:
    await service.record_probe(None, model.id, ok, {"checks": []})


async def _two_chat_models(service: LlmConfigService) -> tuple[LlmModel, LlmModel]:
    """A second local provider with a chat model, and a cloud one."""
    local = await service.create_provider(
        None, ProviderIn(name="vllm", base_url="http://vllm:8000/v1")
    )
    cloud = await service.create_provider(
        None,
        ProviderIn(
            name="cloud", base_url="https://api.example.com/v1", api_key=SecretStr("sk-x" * 5)
        ),
    )
    local_chat = await service.create_model(
        None, ModelIn(provider_id=local.id, model_name="qwen3-32b", capability=LlmCapability.CHAT)
    )
    cloud_chat = await service.create_model(
        None, ModelIn(provider_id=cloud.id, model_name="gpt-x", capability=LlmCapability.CHAT)
    )
    return local_chat, cloud_chat


async def test_seeding_imports_env_as_active_revision_one(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    assert await service.ensure_seeded(None) is True
    assert await service.ensure_seeded(None) is False

    active = await service.active()
    assert active is not None
    assert active.revision.number == 1
    snapshot = ConfigSnapshot.model_validate(active.revision.snapshot)
    models = {
        p: d.model for p, d in ((p, snapshot.deployment_for(p)) for p in snapshot.routes) if d
    }
    assert models == {
        LlmRoutePurpose.PII_SCRUB: "bielik-11b",
        LlmRoutePurpose.PRIVATE_NOTE: "bielik-11b",
        LlmRoutePurpose.PROMOTION: "bielik-11b",
        LlmRoutePurpose.RAG: "bielik-11b",
        LlmRoutePurpose.VLM: "qwen-vl",
        LlmRoutePurpose.EMBED: "gemma-embed",
    }
    assert all(d.kind == LlmProviderKind.LOCAL for d in snapshot.deployments.values())
    (provider,) = await service.providers()
    assert provider.inherit_env_key
    assert provider.api_key_ciphertext is None
    assert service.api_key(provider) == "lm-studio-token-0001"
    embed = snapshot.deployment_for(LlmRoutePurpose.EMBED)
    assert embed is not None
    assert embed.embed_dimension == DIM
    async with factory() as session:
        event = await session.scalar(
            select(SecurityAuditLog).where(
                SecurityAuditLog.event_type == EventType.LLM_CONFIG_ACTIVATED.value
            )
        )
    assert event is not None
    assert event.payload["seeded"] is True


async def test_seeding_without_models_activates_an_empty_revision(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher, chat_model=None, vlm_model=None, embed_model=None)
    assert await service.ensure_seeded(None)
    active = await service.active()
    assert active is not None
    assert active.routing == {}


async def test_provider_endpoints_are_classified_and_refused(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    local = await service.create_provider(
        None, ProviderIn(name="vllm", base_url="http://vllm:8000/v1/")
    )
    cloud = await service.create_provider(
        None, ProviderIn(name="cloud", base_url="https://api.example.com/v1")
    )
    assert (local.kind, local.base_url) == (LlmProviderKind.LOCAL, "http://vllm:8000/v1")
    assert cloud.kind == LlmProviderKind.CLOUD
    for url, code in (
        ("http://postgres:5432", "endpoint_not_allowed"),
        ("http://10.0.0.7:11434/v1", "endpoint_not_allowed"),
        ("http://api.example.com/v1", "cloud_requires_https"),
    ):
        with pytest.raises(ConfigError) as exc:
            await service.create_provider(None, ProviderIn(name="bad", base_url=url))
        assert exc.value.code == code
    with pytest.raises(ConfigError) as exc:
        await service.create_provider(None, ProviderIn(name="vllm", base_url="http://vllm:8000"))
    assert exc.value.code == "name_taken"


async def test_api_keys_are_encrypted_write_only_and_bound_to_their_row(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    key = "sk-proj-very-secret-ABCD"
    a = await service.create_provider(
        None, ProviderIn(name="a", base_url="https://api.example.com/v1", api_key=SecretStr(key))
    )
    b = await service.create_provider(
        None,
        ProviderIn(
            name="b", base_url="https://api.example.com/v2", api_key=SecretStr("other-key-1234")
        ),
    )
    assert a.api_key_ciphertext is not None
    assert key.encode() not in a.api_key_ciphertext
    assert a.api_key_hint == "ABCD"
    assert service.api_key(a) == key

    async with factory() as session, session.begin():
        await session.execute(
            update(LlmProvider)
            .where(LlmProvider.id == b.id)
            .values(api_key_wrapped=a.api_key_wrapped, api_key_ciphertext=a.api_key_ciphertext)
        )
    with pytest.raises(InvalidTag):
        service.api_key(await service.provider(b.id))

    cleared = await service.update_provider(None, a.id, ProviderPatch(clear_api_key=True))
    assert cleared.api_key_ciphertext is None
    assert service.api_key(cleared) is None
    async with factory() as session:
        payloads = [
            str(r.payload)
            for r in await session.scalars(
                select(SecurityAuditLog).where(
                    SecurityAuditLog.event_type == EventType.LLM_PROVIDER_CHANGED.value
                )
            )
        ]
    assert payloads
    assert not any(key in p or "ABCD" in p for p in payloads)


async def test_local_only_purposes_refuse_cloud_models_in_service_and_database(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher, allow_cloud=True)
    await service.ensure_seeded(None)
    _, cloud_chat = await _two_chat_models(service)
    draft = await service.create_draft(None, None, "test")
    for purpose in (
        LlmRoutePurpose.PII_SCRUB,
        LlmRoutePurpose.PRIVATE_NOTE,
        LlmRoutePurpose.PROMOTION,
    ):
        with pytest.raises(ConfigError) as exc:
            await service.set_routing(None, draft.revision.number, {purpose: cloud_chat.id})
        assert exc.value.code == "local_only_purpose"
    # Bypassing the service: the trigger still says no.
    with pytest.raises(DBAPIError, match="LOCAL provider"):
        await _write(
            factory,
            update(LlmRouting)
            .where(
                LlmRouting.revision_id == draft.revision.id,
                LlmRouting.purpose == LlmRoutePurpose.PROMOTION,
            )
            .values(model_id=cloud_chat.id),
        )
    (embed,) = [m for m in await service.models() if m.capability == LlmCapability.EMBED]
    with pytest.raises(DBAPIError, match="cannot use a EMBED model"):
        await _write(
            factory,
            update(LlmRouting)
            .where(
                LlmRouting.revision_id == draft.revision.id,
                LlmRouting.purpose == LlmRoutePurpose.RAG,
            )
            .values(model_id=embed.id),
        )
    # RAG may use the cloud model in a draft.
    view = await service.set_routing(
        None, draft.revision.number, {LlmRoutePurpose.RAG: cloud_chat.id}
    )
    assert view.routing[LlmRoutePurpose.RAG] == cloud_chat.id


async def test_activation_needs_fresh_probes_and_the_cloud_ceiling(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    clock = [datetime.now(UTC)]
    service = LlmConfigService(
        factory,
        cipher,
        ENV.model_copy(update={"allow_cloud": False}),
        vector_size=DIM,
        now=lambda: clock[0],
    )
    await service.ensure_seeded(None)
    local_chat, cloud_chat = await _two_chat_models(service)
    draft = await service.create_draft(None, None, None)
    number = draft.revision.number
    await service.set_routing(None, number, {LlmRoutePurpose.RAG: cloud_chat.id})
    await _probed(service, cloud_chat)
    with pytest.raises(ConfigError) as exc:
        await service.activate(None, number)
    assert exc.value.code == "cloud_not_allowed"

    await service.set_routing(None, number, {LlmRoutePurpose.RAG: local_chat.id})
    with pytest.raises(ConfigError) as exc:
        await service.activate(None, number)
    assert exc.value.code == "probe_required"  # the seeded chat model was never probed
    for model in await service.models():
        await _probed(service, model)
    await _probed(service, local_chat, ok=False)
    with pytest.raises(ConfigError) as exc:
        await service.activate(None, number)
    assert exc.value.detail["model"] == "qwen3-32b"
    await _probed(service, local_chat)
    clock[0] += timedelta(hours=25)
    with pytest.raises(ConfigError):
        await service.activate(None, number)  # stale
    for model in await service.models():
        await _probed(service, model)

    view = await service.activate(None, number)
    assert view.revision.status == LlmRevisionStatus.ACTIVE
    revisions = {r.number: r.status for r in await service.revisions()}
    assert revisions == {1: LlmRevisionStatus.ARCHIVED, number: LlmRevisionStatus.ACTIVE}
    snapshot = ConfigSnapshot.model_validate(view.revision.snapshot)
    rag = snapshot.deployment_for(LlmRoutePurpose.RAG)
    assert rag is not None
    assert rag.model == "qwen3-32b"
    with pytest.raises(ConfigError) as exc:
        await service.activate(None, number)
    assert exc.value.code == "not_draft"


async def test_active_and_archived_revisions_are_immutable(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    await service.ensure_seeded(None)
    active = await service.active()
    assert active is not None
    rev_id = active.revision.id
    statements = [
        update(LlmConfigRevision)
        .where(LlmConfigRevision.id == rev_id)
        .values(snapshot={"routes": {}, "deployments": {}}),
        update(LlmConfigRevision)
        .where(LlmConfigRevision.id == rev_id)
        .values(status=LlmRevisionStatus.DRAFT),
        text("DELETE FROM llm_config_revision WHERE id = :id").bindparams(id=rev_id),
        text("DELETE FROM llm_routing WHERE revision_id = :id").bindparams(id=rev_id),
    ]
    for statement in statements:
        with pytest.raises(DBAPIError, match=r"immutable|cannot be deleted"):
            await _write(factory, statement)
    with pytest.raises(ConfigError) as exc:
        await service.set_routing(None, 1, {LlmRoutePurpose.RAG: None})
    assert exc.value.code == "not_draft"


async def test_a_tampered_snapshot_cannot_route_private_data_to_the_cloud(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher, allow_cloud=True)
    _, cloud_chat = await _two_chat_models(service)
    deployment = {
        "provider_id": str(cloud_chat.provider_id),
        "provider_name": "cloud",
        "model_id": str(cloud_chat.id),
        "model": "gpt-x",
        "capability": "CHAT",
        "base_url": "https://api.example.com/v1",
        "kind": "LOCAL",  # lie
    }
    snapshot = {"version": 1, "routes": {"PRIVATE_NOTE": "d"}, "deployments": {"d": deployment}}
    with pytest.raises(DBAPIError, match="not LOCAL"):
        await _write(
            factory,
            insert(LlmConfigRevision).values(
                id=uuid7(), status=LlmRevisionStatus.ACTIVE, snapshot=snapshot
            ),
        )


async def test_a_provider_serving_local_only_purposes_cannot_turn_cloud(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    await service.ensure_seeded(None)
    (provider,) = await service.providers()
    with pytest.raises(ConfigError) as exc:
        await service.update_provider(
            None, provider.id, ProviderPatch(base_url="https://api.example.com/v1")
        )
    assert exc.value.code == "local_only_purpose"
    with pytest.raises(DBAPIError, match="cannot become CLOUD"):
        await _write(
            factory,
            update(LlmProvider)
            .where(LlmProvider.id == provider.id)
            .values(kind=LlmProviderKind.CLOUD),
        )
    with pytest.raises(ConfigError) as exc:
        await service.set_provider_disabled(None, provider.id, True)
    assert exc.value.code == "provider_in_use"


async def _activate_with(
    service: LlmConfigService, routes: dict[LlmRoutePurpose, UUID | None]
) -> int:
    draft = await service.create_draft(None, None, None)
    await service.set_routing(None, draft.revision.number, dict(routes))
    for model in await service.models():
        await _probed(service, model)
    return (await service.activate(None, draft.revision.number)).revision.number


async def test_embedding_model_changes_are_refused(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    await service.ensure_seeded(None)
    (provider,) = await service.providers()
    other = await service.create_model(
        None,
        ModelIn(
            provider_id=provider.id,
            model_name="bge-m3",
            capability=LlmCapability.EMBED,
            embed_dimension=DIM,
        ),
    )
    with pytest.raises(ConfigError) as exc:
        await _activate_with(service, {LlmRoutePurpose.EMBED: other.id})
    assert exc.value.code == "embed_change_requires_reindex"
    with pytest.raises(ConfigError) as exc:
        await _activate_with(service, {LlmRoutePurpose.EMBED: None})
    assert exc.value.code == "embed_change_requires_reindex"


async def test_rollback_restores_a_snapshot_as_a_new_revision(
    factory: SessionFactory, cipher: EnvelopeCipher
) -> None:
    service = _service(factory, cipher)
    await service.ensure_seeded(None)
    local_chat, _ = await _two_chat_models(service)
    second = await _activate_with(service, {LlmRoutePurpose.RAG: local_chat.id})

    restored = await service.rollback(None, 1, None)
    assert restored.revision.number == second + 1
    assert restored.revision.rolled_back_from == 1
    assert restored.revision.status == LlmRevisionStatus.ACTIVE
    snapshot = ConfigSnapshot.model_validate(restored.revision.snapshot)
    rag = snapshot.deployment_for(LlmRoutePurpose.RAG)
    assert rag is not None
    assert rag.model == "bielik-11b"
    statuses = {r.number: r.status for r in await service.revisions()}
    assert statuses[second] == LlmRevisionStatus.ARCHIVED
    with pytest.raises(ConfigError) as exc:
        await service.rollback(None, restored.revision.number, None)
    assert exc.value.code == "not_archived"
    with pytest.raises(ConfigError):
        await service.set_model_disabled(None, (await service.models())[0].id, True)
