"""Chunk 2.3: the real clamd of the dev stack (loopback port 3310) detects EICAR."""

from __future__ import annotations

import io

import pytest

from spoldzielnia_ai.core.settings import ClamavSettings
from spoldzielnia_ai.files.antivirus import ClamdScanner
from tests.integration.stack import require_port

pytestmark = pytest.mark.integration

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest.fixture
def scanner() -> ClamdScanner:
    require_port(3310)
    return ClamdScanner(ClamavSettings(host="127.0.0.1", port=3310))


async def test_eicar_detected(scanner: ClamdScanner) -> None:
    assert await scanner.ping()
    result = await scanner.scan(io.BytesIO(EICAR))
    assert not result.clean
    assert result.signature is not None
    assert "Eicar" in result.signature


async def test_clean_file(scanner: ClamdScanner) -> None:
    assert (await scanner.scan(io.BytesIO(b"Zwykla notatka o pszczolach\n" * 1000))).clean


async def test_large_file_within_configured_limit(scanner: ClamdScanner) -> None:
    """30 MB is above clamd's 25 MB default StreamMaxLength: the compose limits must apply."""
    result = await scanner.scan(io.BytesIO(b"\x00" * (30 * 1024 * 1024)))
    assert result.clean
