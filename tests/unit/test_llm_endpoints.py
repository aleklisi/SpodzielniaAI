"""Chunk 7.1: which LLM endpoints an admin may configure, and whether they count as local."""

from __future__ import annotations

import importlib

import pytest

from spoldzielnia_ai.core.settings import LlmSettings
from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind, LlmRoutePurpose
from spoldzielnia_ai.llm.config_service import PRIVATE_ROUTE_PURPOSES, capability_for
from spoldzielnia_ai.llm.endpoints import (
    EndpointNotAllowed,
    allowed_endpoints,
    classify,
    may_receive_private_data,
    normalize_base_url,
    private_data_endpoints,
)
from spoldzielnia_ai.llm.gateway import PRIVATE_PURPOSES, LLMPurpose

SETTINGS = LlmSettings(
    local_base_url="http://llm:11434/v1",
    local_endpoints=["http://vllm:8000", "http://host.docker.internal:1234"],
    cloud_hosts=["api.openai.com", "API.Example.COM."],
    cloud_base_url="https://api.mistral.ai/v1",
)


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("http://llm:11434/v1", LlmProviderKind.LOCAL),
        ("http://LLM:11434/other/path/", LlmProviderKind.LOCAL),  # origin match, any path
        ("http://vllm:8000/v1", LlmProviderKind.LOCAL),
        ("http://vllm:80/v1", None),  # another port is another origin
        ("http://host.docker.internal:1234/v1", LlmProviderKind.LOCAL),
        ("https://api.openai.com/v1", LlmProviderKind.CLOUD),
        ("https://api.example.com/v1", LlmProviderKind.CLOUD),
        ("https://api.mistral.ai/v1", LlmProviderKind.CLOUD),  # from cloud_base_url
        ("https://evil.example.org/v1", None),
        ("http://10.0.0.5:11434/v1", None),  # private range, not on the list
        ("http://127.0.0.1:8000/v1", None),
        ("http://169.254.169.254/latest", None),
        ("http://postgres:5432", None),
        ("http://api:8000/admin", None),
    ],
)
def test_classification_follows_the_allowlists(url: str, kind: LlmProviderKind | None) -> None:
    if kind is None:
        with pytest.raises(EndpointNotAllowed) as exc:
            classify(url, SETTINGS)
        assert exc.value.reason == "endpoint_not_allowed"
    else:
        assert classify(url, SETTINGS) == kind


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://api.openai.com/v1", "cloud_requires_https"),
        ("https://api.openai.com:8443/v1", "cloud_requires_https"),
        ("https://user:pw@api.openai.com/v1", "credentials_in_url"),
        ("https://api.openai.com/v1?x=1", "invalid_url"),
        ("https://api.openai.com/v1#frag", "invalid_url"),
        ("ftp://llm:11434/v1", "invalid_url"),
        ("file:///etc/passwd", "invalid_url"),
        ("http://llm:11434/v1\r\nHost: x", "invalid_url"),
        ("http://llm:99999/v1", "invalid_url"),
        ("llm:11434/v1", "invalid_url"),
        ("https://" + "a" * 600 + ".com", "invalid_url"),
    ],
)
def test_malformed_or_unsafe_urls_are_refused(url: str, reason: str) -> None:
    with pytest.raises(EndpointNotAllowed) as exc:
        classify(url, SETTINGS)
    assert exc.value.reason == reason


def test_a_cloud_host_cannot_be_reached_as_local_through_a_local_port() -> None:
    """Locality is the origin's, so listing a host as cloud never makes it local."""
    settings = LlmSettings(cloud_hosts=["llm"], local_base_url="http://llm:11434/v1")
    assert classify("http://llm:11434/v1", settings) == LlmProviderKind.LOCAL
    with pytest.raises(EndpointNotAllowed):
        classify("http://llm:8080/v1", settings)


def test_defaults_allow_only_the_compose_llm_service() -> None:
    settings = LlmSettings()
    assert allowed_endpoints(settings) == (["http://llm:11434"], [])
    assert classify("http://llm:11434/v1", settings) == LlmProviderKind.LOCAL
    with pytest.raises(EndpointNotAllowed):
        classify("https://api.openai.com/v1", settings)


def test_normalize_strips_trailing_slashes() -> None:
    assert normalize_base_url(" http://llm:11434/v1// ") == "http://llm:11434/v1"


def test_private_purposes_match_the_gateway_and_the_migration() -> None:
    migration = importlib.import_module(
        "spoldzielnia_ai.db.migrations.versions.0022_transcribe_purpose"
    )
    gateway = {p.value for p in PRIVATE_PURPOSES}
    assert {p.value for p in PRIVATE_ROUTE_PURPOSES} == gateway
    assert set(migration.PRIVATE) == gateway
    assert {p.value for p in LlmRoutePurpose} == {p.value for p in LLMPurpose} - {"PLAYGROUND"}


def test_capability_per_purpose() -> None:
    assert capability_for(LlmRoutePurpose.VLM) == LlmCapability.VLM
    assert capability_for(LlmRoutePurpose.EMBED) == LlmCapability.EMBED
    assert capability_for(LlmRoutePurpose.TRANSCRIBE) == LlmCapability.TRANSCRIBE
    assert capability_for(LlmRoutePurpose.RAG) == LlmCapability.CHAT
    assert capability_for(LlmRoutePurpose.PII_SCRUB) == LlmCapability.CHAT


def test_private_data_endpoints_default_to_the_local_ones() -> None:
    assert private_data_endpoints(SETTINGS) == [
        "http://host.docker.internal:1234",
        "http://llm:11434",
        "http://vllm:8000",
    ]
    assert may_receive_private_data("http://vllm:8000/v1", SETTINGS)
    assert not may_receive_private_data("https://api.mistral.ai/v1", SETTINGS)


def test_the_operator_list_replaces_the_default() -> None:
    settings = SETTINGS.model_copy(
        update={"private_data_endpoints": ["https://api.mistral.ai", "not a url"]}
    )
    assert private_data_endpoints(settings) == ["https://api.mistral.ai"]
    assert may_receive_private_data("https://API.mistral.ai/v1", settings)
    assert not may_receive_private_data("http://api.mistral.ai/v1", settings)  # scheme counts
    assert not may_receive_private_data("https://api.mistral.ai:8443/v1", settings)
    assert not may_receive_private_data("http://vllm:8000/v1", settings)
    none = SETTINGS.model_copy(update={"private_data_endpoints": []})
    assert private_data_endpoints(none) == []
    assert not may_receive_private_data("http://llm:11434/v1", none)
