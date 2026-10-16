"""Chunk 3.4: image description through the gateway (VLM, always local)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from spoldzielnia_ai.llm.gateway import LLMPurpose
from spoldzielnia_ai.workers.engines.base import PermanentError
from spoldzielnia_ai.workers.engines.vision import GatewayVisionEngine
from tests.fixtures import files
from tests.fixtures.llm import FakeLLMGateway

if TYPE_CHECKING:
    from pathlib import Path


def _image(tmp_path: Path) -> Path:
    path = tmp_path / "ul.png"
    path.write_bytes(files.png())
    return path


def test_image_is_described_locally_inline(tmp_path: Path) -> None:
    gateway = FakeLLMGateway(reply=lambda call: "Ramka z zasklepionym czerwiem.")
    text = GatewayVisionEngine(lambda: gateway).extract(_image(tmp_path), "image/png")

    assert text == "Ramka z zasklepionym czerwiem."
    (call,) = gateway.calls
    assert call.purpose == LLMPurpose.VLM
    assert call.force_local_llm is True
    content = call.messages[1]["content"]
    assert isinstance(content, list)
    image = next(part for part in content if part["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")  # never a link


def test_no_local_vlm_is_an_explicit_failure(tmp_path: Path) -> None:
    gateway = FakeLLMGateway(local_available=False)
    with pytest.raises(PermanentError, match="vision_unavailable"):
        GatewayVisionEngine(lambda: gateway).extract(_image(tmp_path), "image/png")


def test_fake_gateway_enforces_the_local_flag() -> None:
    import asyncio

    gateway = FakeLLMGateway()
    with pytest.raises(AssertionError, match="force_local_llm"):
        asyncio.run(gateway.complete([], purpose=LLMPurpose.PROMOTION))
    assert gateway.calls == []
