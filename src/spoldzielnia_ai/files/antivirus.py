"""ClamAV scanning over clamd's ``INSTREAM`` protocol (typed client, no ``pyclamd``).

Fail-closed: any failure to get a definite verdict (connection refused, timeout, protocol
error, size limit) raises ``ScannerUnavailable``, and the upload is refused, never stored
unscanned. clamd runs with ``AlertExceedsMax``, so files beyond its scan limits come back as
``FOUND`` (``Heuristics.Limits.Exceeded``) instead of a silent ``OK``.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import ClamavSettings

_CHUNK = 256 * 1024
_MAX_REPLY = 4096


class ScannerUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class VirusScanner(Protocol):
    async def scan(self, src: IO[bytes]) -> ScanResult: ...


def parse_reply(reply: bytes) -> ScanResult:
    text = reply.rstrip(b"\x00\n").decode("utf-8", errors="replace")
    # "stream: OK" | "stream: <Signature> FOUND" | "<message> ERROR"
    if text.endswith(" ERROR") or not text.startswith("stream: "):
        raise ScannerUnavailable(f"clamd error: {text[:120]}")
    verdict = text.removeprefix("stream: ")
    if verdict == "OK":
        return ScanResult(clean=True)
    if verdict.endswith(" FOUND"):
        return ScanResult(clean=False, signature=verdict.removesuffix(" FOUND")[:120])
    raise ScannerUnavailable(f"unexpected clamd reply: {text[:120]}")


class ClamdScanner:
    def __init__(self, settings: ClamavSettings) -> None:
        self._settings = settings

    async def ping(self) -> bool:
        try:
            async with asyncio.timeout(self._settings.connect_timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self._settings.host, self._settings.port
                )
                writer.write(b"zPING\x00")
                await writer.drain()
                reply = await reader.read(16)
                writer.close()
                await writer.wait_closed()
        except (OSError, TimeoutError):
            return False
        return reply.rstrip(b"\x00") == b"PONG"

    async def scan(self, src: IO[bytes]) -> ScanResult:
        try:
            async with asyncio.timeout(self._settings.connect_timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self._settings.host, self._settings.port
                )
        except (OSError, TimeoutError) as exc:
            raise ScannerUnavailable(f"clamd unreachable: {type(exc).__name__}") from exc
        try:
            async with asyncio.timeout(self._settings.scan_timeout_seconds):
                writer.write(b"zINSTREAM\x00")
                sent = 0
                while chunk := await asyncio.to_thread(src.read, _CHUNK):
                    sent += len(chunk)
                    if sent > self._settings.stream_max_bytes:
                        raise ScannerUnavailable("file exceeds the scanner's stream limit")
                    writer.write(struct.pack(">I", len(chunk)) + chunk)
                    await writer.drain()
                writer.write(struct.pack(">I", 0))
                await writer.drain()
                reply = await reader.read(_MAX_REPLY)
        except (OSError, TimeoutError) as exc:
            raise ScannerUnavailable(f"clamd scan failed: {type(exc).__name__}") from exc
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if not reply:
            raise ScannerUnavailable("clamd closed the connection")
        return parse_reply(reply)
