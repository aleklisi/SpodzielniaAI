"""Chunk 7.4: the ``/admin/llm`` API (ADMIN + MFA): providers, models, probes, revisions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.models import SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.llm.gateway import LLMPurpose
from tests.fixtures.app import harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

DIM = 1024  # QdrantSettings default vector_size
LLM = LlmSettings(
    chat_model="bielik-11b",
    embed_model="gemma-embed",
    api_key=SecretStr("env-token-000000"),
    local_endpoints=["http://vllm:8000"],
    cloud_hosts=["api.example.com"],
    playground_per_hour=50,
)
SECRET = "sk-live-do-not-leak-WXYZ"  # noqa: S105 - a test value


class ProbeTransport:
    """Answers the synthetic probes like a well-behaved model (no network)."""

    def __init__(self, reject_json_schema: bool = False) -> None:
        self.reject_json_schema = reject_json_schema
        self.calls: list[str] = []

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(model)
        if "response_format" in kwargs and self.reject_json_schema:
            error = Exception("grammar")
            error.status_code = 400  # pyright: ignore[reportAttributeAccessIssue]
            raise error
        text = json.dumps(messages, ensure_ascii=False)
        content = '{"city": "Kraków", "country": "Polska"}' if "city" in text else "Warszawa"
        usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any:
        vectors = [[1.0 if i == n else 0.0 for i in range(DIM)] for n in range(len(input))]
        return SimpleNamespace(data=[{"embedding": v} for v in vectors])


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


def _settings(settings: Settings, **llm: Any) -> Settings:
    return settings.model_copy(update={"llm": LLM.model_copy(update=llm)})


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    async with harness(_settings(settings), factory) as h:
        h.app.state.llm_probe_transport = ProbeTransport()
        yield h


async def _admin(h: Harness) -> LoggedIn:
    return await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"])


async def test_authorization_matrix(h: Harness) -> None:
    member = await h.login()
    expert = await h.login(roles=["EXPERT", "MEMBER"], amr=["pwd", "otp"])
    admin_without_mfa = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd"])
    admin = await _admin(h)
    body = {"name": "x", "base_url": "http://vllm:8000/v1"}
    for user, expected in ((member, 403), (expert, 403), (admin_without_mfa, 403)):
        for path in ("/admin/llm/status", "/admin/llm/providers", "/admin/llm/revisions"):
            assert (await user.client.get(path)).status_code == expected
        created = await user.client.post("/admin/llm/providers", json=body, headers=user.headers)
        assert created.status_code == expected
    assert (await admin.client.get("/admin/llm/status")).status_code == 200


async def test_first_visit_shows_the_env_configuration_as_revision_one(h: Harness) -> None:
    admin = await _admin(h)
    status = (await admin.client.get("/admin/llm/status")).json()
    assert status["active_revision"] == 1
    assert status["process_revision"] == 1  # the API process already routes by it
    assert status["allow_cloud"] is False
    assert status["local_endpoints"] == ["http://llm:11434", "http://vllm:8000"]
    local_only = {p["purpose"]: p["local_only"] for p in status["purposes"]}
    assert local_only == {
        "PII_SCRUB": True,
        "PRIVATE_NOTE": True,
        "PROMOTION": True,
        "VLM": True,
        "EMBED": True,
        "RAG": False,
    }
    (revision,) = (await admin.client.get("/admin/llm/revisions")).json()
    assert revision["status"] == "ACTIVE"
    assert revision["routes"]["RAG"]["name"] == "local/bielik-11b"
    (provider,) = (await admin.client.get("/admin/llm/providers")).json()
    assert provider["inherit_env_key"] is True
    assert provider["in_active"] is True


async def test_keys_are_write_only_and_urls_are_checked(
    h: Harness, factory: SessionFactory
) -> None:
    admin = await _admin(h)
    created = await admin.client.post(
        "/admin/llm/providers",
        json={"name": "cloud", "base_url": "https://api.example.com/v1", "api_key": SECRET},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    listed = await admin.client.get("/admin/llm/providers")
    for text in (created.text, listed.text):
        assert SECRET not in text
    cloud = created.json()
    assert (cloud["kind"], cloud["key_set"], cloud["key_hint"]) == ("CLOUD", True, "WXYZ")

    for url, code in (
        ("http://postgres:5432", "endpoint_not_allowed"),
        ("http://169.254.169.254/latest", "endpoint_not_allowed"),
        ("https://user:pw@api.example.com/v1", "credentials_in_url"),
    ):
        refused = await admin.client.post(
            "/admin/llm/providers", json={"name": "bad", "base_url": url}, headers=admin.headers
        )
        assert refused.status_code == 422
        assert refused.json()["detail"]["error"] == code
    # Cloud is stored, but nothing may go there without LLM_ALLOW_CLOUD.
    test = await admin.client.post(
        f"/admin/llm/providers/{cloud['id']}/test", headers=admin.headers
    )
    assert test.status_code == 422
    assert test.json()["detail"]["error"] == "cloud_not_allowed"
    async with factory() as session:
        payloads = [str(r.payload) for r in await session.scalars(select(SecurityAuditLog))]
    assert not any(SECRET in p for p in payloads)


async def test_connection_test_lists_the_models(h: Harness) -> None:
    admin = await _admin(h)
    provider = (
        await admin.client.post(
            "/admin/llm/providers",
            json={"name": "vllm", "base_url": "http://vllm:8000/v1", "api_key": "local-token-1234"},
            headers=admin.headers,
        )
    ).json()
    route = h.router.get("http://vllm:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen3-32b"}, {"id": "bge-m3"}]})
    )
    result = (
        await admin.client.post(
            f"/admin/llm/providers/{provider['id']}/test", headers=admin.headers
        )
    ).json()
    assert result["ok"] is True
    assert result["models"] == ["bge-m3", "qwen3-32b"]
    assert route.calls.last.request.headers["authorization"] == "Bearer local-token-1234"
    h.router.get("http://vllm:8000/v1/models").mock(return_value=httpx.Response(401))
    failed = (
        await admin.client.post(
            f"/admin/llm/providers/{provider['id']}/test", headers=admin.headers
        )
    ).json()
    assert (failed["ok"], failed["status"]) == (False, 401)


async def _model(
    admin: LoggedIn, provider: str, name: str, capability: str = "CHAT"
) -> dict[str, Any]:
    created = await admin.client.post(
        "/admin/llm/models",
        json={"provider_id": provider, "model_name": name, "capability": capability},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _vllm(admin: LoggedIn) -> str:
    created = await admin.client.post(
        "/admin/llm/providers",
        json={"name": "vllm", "base_url": "http://vllm:8000/v1"},
        headers=admin.headers,
    )
    return created.json()["id"]


async def test_probes_report_and_recommend_a_structured_output_mode(h: Harness) -> None:
    admin = await _admin(h)
    chat = await _model(admin, await _vllm(admin), "qwen3-32b")
    probed = (
        await admin.client.post(f"/admin/llm/models/{chat['id']}/probe", headers=admin.headers)
    ).json()
    report = probed["report"]
    assert report["ok"] is True
    assert report["recommended_structured_output"] == "json_schema"
    assert [c["name"] for c in report["checks"]] == ["reply", "json_json_schema", "json_prompt"]
    assert report["checks"][0]["polish_answer"] is True
    assert probed["model"]["last_probe_ok"] is True
    assert "Warszawa" not in json.dumps(probed)  # no model output stored or returned

    h.app.state.llm_probe_transport = ProbeTransport(reject_json_schema=True)
    report = (
        await admin.client.post(f"/admin/llm/models/{chat['id']}/probe", headers=admin.headers)
    ).json()["report"]
    assert report["ok"] is True
    assert report["recommended_structured_output"] == "prompt"
    assert report["checks"][1]["cause"] == "Exception"


async def test_embedding_probe_checks_the_dimension(h: Harness) -> None:
    admin = await _admin(h)
    (local,) = (await admin.client.get("/admin/llm/providers")).json()
    embed = await _model(admin, local["id"], "bge-small", "EMBED")
    report = (
        await admin.client.post(f"/admin/llm/models/{embed['id']}/probe", headers=admin.headers)
    ).json()["report"]
    assert (report["dimension"], report["dimension_matches"], report["separates_unrelated"]) == (
        DIM,
        True,
        True,
    )


async def test_draft_probe_activate_and_roll_back(h: Harness, factory: SessionFactory) -> None:
    admin = await _admin(h)
    chat = await _model(admin, await _vllm(admin), "qwen3-32b")
    draft = (
        await admin.client.post(
            "/admin/llm/revisions", json={"comment": "test"}, headers=admin.headers
        )
    ).json()
    number = draft["number"]
    assert draft["status"] == "DRAFT"
    assert set(draft["routing"]) == {"PII_SCRUB", "PRIVATE_NOTE", "PROMOTION", "RAG", "EMBED"}
    routed = await admin.client.put(
        f"/admin/llm/revisions/{number}/routing",
        json={"routes": {"RAG": chat["id"]}},
        headers=admin.headers,
    )
    assert routed.json()["routing"]["RAG"] == chat["id"]

    blocked = await admin.client.post(
        f"/admin/llm/revisions/{number}/activate", headers=admin.headers
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"] == "probe_required"
    for model in (await admin.client.get("/admin/llm/models")).json():
        response = await admin.client.post(
            f"/admin/llm/models/{model['id']}/probe", headers=admin.headers
        )
        assert response.json()["report"]["ok"], response.text
    activated = await admin.client.post(
        f"/admin/llm/revisions/{number}/activate", headers=admin.headers
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["routes"]["RAG"]["name"] == "vllm/qwen3-32b"
    config = await h.app.state.llm_config.current()
    assert config.revision == number
    assert config.routes[LLMPurpose.RAG] == "vllm/qwen3-32b"
    assert config.routes[LLMPurpose.PROMOTION] == "local/bielik-11b"

    restored = await admin.client.post(
        "/admin/llm/revisions/1/rollback", json={"comment": "back"}, headers=admin.headers
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["rolled_back_from"] == 1
    assert (await h.app.state.llm_config.current()).routes[LLMPurpose.RAG] == "local/bielik-11b"
    async with factory() as session:
        events = [
            (r.event_type, str(r.actor_id))
            for r in await session.scalars(
                select(SecurityAuditLog).where(
                    SecurityAuditLog.event_type.in_(
                        [
                            EventType.LLM_CONFIG_ACTIVATED.value,
                            EventType.LLM_CONFIG_ROLLED_BACK.value,
                        ]
                    )
                )
            )
        ]
    assert events[-2:] == [
        (EventType.LLM_CONFIG_ACTIVATED.value, admin.user_id),
        (EventType.LLM_CONFIG_ROLLED_BACK.value, admin.user_id),
    ]


async def test_policy_errors_are_explained(h: Harness) -> None:
    admin = await _admin(h)
    cloud = (
        await admin.client.post(
            "/admin/llm/providers",
            json={"name": "cloud", "base_url": "https://api.example.com/v1"},
            headers=admin.headers,
        )
    ).json()
    gpt = await _model(admin, cloud["id"], "gpt-x")
    draft = (await admin.client.post("/admin/llm/revisions", json={}, headers=admin.headers)).json()
    response = await admin.client.put(
        f"/admin/llm/revisions/{draft['number']}/routing",
        json={"routes": {"PRIVATE_NOTE": gpt["id"]}},
        headers=admin.headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {"error": "local_only_purpose", "purpose": "PRIVATE_NOTE"}

    (local,) = [
        p for p in (await admin.client.get("/admin/llm/providers")).json() if p["name"] == "local"
    ]
    other_embed = await _model(admin, local["id"], "bge-m3", "EMBED")
    await admin.client.put(
        f"/admin/llm/revisions/{draft['number']}/routing",
        json={"routes": {"EMBED": other_embed["id"], "RAG": gpt["id"]}},
        headers=admin.headers,
    )
    refused = await admin.client.post(
        f"/admin/llm/revisions/{draft['number']}/activate", headers=admin.headers
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "cloud_not_allowed"
    await admin.client.put(
        f"/admin/llm/revisions/{draft['number']}/routing",
        json={"routes": {"RAG": None}},
        headers=admin.headers,
    )
    for model in (await admin.client.get("/admin/llm/models")).json():
        if model["provider_id"] == local["id"]:
            await admin.client.post(f"/admin/llm/models/{model['id']}/probe", headers=admin.headers)
    refused = await admin.client.post(
        f"/admin/llm/revisions/{draft['number']}/activate", headers=admin.headers
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["error"] == "embed_change_requires_reindex"
    deleted = await admin.client.delete(
        f"/admin/llm/revisions/{draft['number']}", headers=admin.headers
    )
    assert deleted.status_code == 204
    gone = await admin.client.delete("/admin/llm/revisions/1", headers=admin.headers)
    assert gone.status_code == 409


async def test_probes_are_rate_limited(settings: Settings, factory: SessionFactory) -> None:
    async with harness(_settings(settings, playground_per_hour=1), factory) as h:
        h.app.state.llm_probe_transport = ProbeTransport()
        admin = await _admin(h)
        chat = (await admin.client.get("/admin/llm/models")).json()[0]
        codes = [
            (
                await admin.client.post(
                    f"/admin/llm/models/{chat['id']}/probe", headers=admin.headers
                )
            ).status_code
            for _ in range(3)
        ]
    assert codes == [200, 200, 429]
