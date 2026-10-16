"""Chunk 2.3: clamd INSTREAM client against an in-process fake clamd."""

from __future__ import annotations

import asyncio
import io
import struct
from typing import TYPE_CHECKING

import pytest

from spoldzielnia_ai.core.settings import ClamavSettings
from spoldzielnia_ai.files.antivirus import ClamdScanner, ScannerUnavailable, parse_reply

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeClamd:
    """Speaks just enough of clamd: zPING and zINSTREAM with length-prefixed chunks."""

    def __init__(self) -> None:
        self.received = b""
        self.reply: Callable[[bytes], bytes] = lambda data: (
            b"stream: Eicar-Test-Signature FOUND\x00" if EICAR in data else b"stream: OK\x00"
        )
        self.hang = False

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        command = await reader.readuntil(b"\x00")
        if command == b"zPING\x00":
            writer.write(b"PONG\x00")
        elif command == b"zINSTREAM\x00":
            data = b""
            while True:
                (length,) = struct.unpack(">I", await reader.readexactly(4))
                if length == 0:
                    break
                data += await reader.readexactly(length)
            self.received = data
            if self.hang:
                await asyncio.sleep(1.5)
            writer.write(self.reply(data))
        await writer.drain()
        writer.close()


@pytest.fixture
async def clamd() -> AsyncIterator[tuple[FakeClamd, ClamavSettings]]:
    fake = FakeClamd()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = ClamavSettings(host="127.0.0.1", port=port, scan_timeout_seconds=1.0)
    async with server:
        yield fake, settings


async def test_clean_file(clamd: tuple[FakeClamd, ClamavSettings]) -> None:
    fake, settings = clamd
    data = b"zwykly plik " * 100_000  # > one chunk
    result = await ClamdScanner(settings).scan(io.BytesIO(data))
    assert result.clean
    assert fake.received == data


async def test_eicar_is_found(clamd: tuple[FakeClamd, ClamavSettings]) -> None:
    _, settings = clamd
    result = await ClamdScanner(settings).scan(io.BytesIO(b"prefix " + EICAR))
    assert not result.clean
    assert result.signature == "Eicar-Test-Signature"


async def test_ping(clamd: tuple[FakeClamd, ClamavSettings]) -> None:
    assert await ClamdScanner(clamd[1]).ping()
    assert not await ClamdScanner(ClamavSettings(host="127.0.0.1", port=1)).ping()


async def test_unreachable_scanner_fails_closed() -> None:
    with pytest.raises(ScannerUnavailable, match="unreachable"):
        await ClamdScanner(ClamavSettings(host="127.0.0.1", port=1)).scan(io.BytesIO(b"x"))


async def test_hung_scanner_times_out(clamd: tuple[FakeClamd, ClamavSettings]) -> None:
    fake, settings = clamd
    fake.hang = True
    with pytest.raises(ScannerUnavailable):
        await ClamdScanner(settings).scan(io.BytesIO(b"x"))


async def test_file_beyond_stream_limit_is_refused(clamd: tuple[FakeClamd, ClamavSettings]) -> None:
    _, settings = clamd
    small = settings.model_copy(update={"stream_max_bytes": 10})
    with pytest.raises(ScannerUnavailable, match="stream limit"):
        await ClamdScanner(small).scan(io.BytesIO(b"x" * 11))


@pytest.mark.parametrize(
    "reply",
    [b"INSTREAM size limit exceeded. ERROR\x00", b"garbage\x00", b"stream: maybe\x00"],
)
def test_error_replies_fail_closed(reply: bytes) -> None:
    with pytest.raises(ScannerUnavailable):
        parse_reply(reply)


def test_limits_exceeded_heuristic_is_a_finding() -> None:
    result = parse_reply(b"stream: Heuristics.Limits.Exceeded.MaxFileSize FOUND\x00")
    assert not result.clean
