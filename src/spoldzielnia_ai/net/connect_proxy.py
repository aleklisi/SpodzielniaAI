"""``llm-egress``: the only way out to cloud LLM APIs (chunk 7.3).

An HTTPS forward proxy that knows one verb: ``CONNECT host:443``. The api container has no
route to the internet; cloud deployments reach their API through this service (on ``backend``
and ``egress``), and nothing else does.

- Only hosts on the cloud allowlist (``llm.cloud_hosts`` and ``llm.cloud_base_url``'s host),
  only port 443. Everything else: 403. Other methods (plain-HTTP proxying): 405.
- DNS is resolved once; if **any** address is not public (``safe_fetch.is_public_ip``: private,
  loopback, link-local, metadata, NAT64 …) the request is refused, and the tunnel goes to the
  validated address, so a rebinding answer is never used.
- TLS stays end to end (the proxy sees no content). Limits on the request head, connect and
  idle time, and concurrent tunnels. Logs carry host, outcome, byte counts and duration.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.net.safe_fetch import is_public_ip, system_resolver

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from spoldzielnia_ai.net.safe_fetch import IPAddress

    Resolver = Callable[[str, int], Awaitable[list[IPAddress]]]
    Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

log = get_logger(__name__)

MAX_HEAD = 8192
CHUNK = 64 * 1024
TLS_PORT = 443


class Denied(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


_STATUS_TEXT = {
    400: "Bad Request",
    403: "Forbidden",
    405: "Method Not Allowed",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def parse_connect(head: bytes) -> tuple[str, int]:
    """``CONNECT host:port HTTP/1.x`` → (host, port). Raises ``Denied``."""
    try:
        line = head.split(b"\r\n", 1)[0].decode("ascii")
    except UnicodeDecodeError:
        raise Denied(400, "bad_request") from None
    parts = line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise Denied(400, "bad_request")
    method, authority, _ = parts
    if method != "CONNECT":
        raise Denied(405, "method_not_allowed")
    host, sep, port_text = authority.rpartition(":")
    if not sep or not host or not port_text.isdigit() or host.startswith("["):
        raise Denied(400, "bad_authority")
    return host.lower().rstrip("."), int(port_text)


class ConnectProxy:
    def __init__(
        self,
        allowed_hosts: frozenset[str],
        *,
        resolver: Resolver = system_resolver,
        opener: Opener | None = None,
        header_timeout: float = 10.0,
        connect_timeout: float = 10.0,
        idle_timeout: float = 300.0,
        max_connections: int = 64,
    ) -> None:
        self._allowed = frozenset(h.lower().rstrip(".") for h in allowed_hosts)
        self._resolver = resolver
        self._opener: Opener = opener or (lambda host, port: asyncio.open_connection(host, port))
        self._header_timeout = header_timeout
        self._connect_timeout = connect_timeout
        self._idle_timeout = idle_timeout
        self._slots = asyncio.Semaphore(max_connections)

    async def _target(self, host: str, port: int) -> IPAddress:
        if host not in self._allowed:
            raise Denied(403, "host_not_allowed")
        if port != TLS_PORT:
            raise Denied(403, "port_not_allowed")
        try:
            addresses = await asyncio.wait_for(
                self._resolver(host, port), timeout=self._connect_timeout
            )
        except (OSError, TimeoutError):
            raise Denied(502, "dns_failed") from None
        if not addresses:
            raise Denied(502, "dns_failed")
        if not all(is_public_ip(address) for address in addresses):
            raise Denied(403, "non_public_address")
        return addresses[0]

    async def handle(
        self, client_reader: asyncio.StreamReader, client: asyncio.StreamWriter
    ) -> None:
        started = time.monotonic()
        host = "-"
        if self._slots.locked():
            await _deny(client, Denied(503, "too_many_connections"), host)
            return
        async with self._slots:
            try:
                try:
                    head = await asyncio.wait_for(
                        client_reader.readuntil(b"\r\n\r\n"), timeout=self._header_timeout
                    )
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                    raise Denied(400, "bad_request") from None
                host, port = parse_connect(head)
                address = await self._target(host, port)
                try:
                    upstream_reader, upstream = await asyncio.wait_for(
                        self._opener(str(address), port), timeout=self._connect_timeout
                    )
                except TimeoutError:
                    raise Denied(504, "connect_timeout") from None
                except OSError:
                    raise Denied(502, "connect_failed") from None
            except Denied as denied:
                await _deny(client, denied, host)
                return
            client.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client.drain()
            sent, received = await asyncio.gather(
                self._pipe(client_reader, upstream),
                self._pipe(upstream_reader, client),
            )
            log.info(
                "egress.tunnel",
                host=host,
                sent=sent,
                received=received,
                seconds=round(time.monotonic() - started, 3),
            )

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
        total = 0
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(CHUNK), timeout=self._idle_timeout)
                if not chunk:
                    break
                total += len(chunk)
                writer.write(chunk)
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
        return total

    async def serve(self, host: str, port: int) -> asyncio.Server:
        server = await asyncio.start_server(self.handle, host, port, limit=MAX_HEAD)
        log.info("egress.listening", host=host, port=port, allowed=sorted(self._allowed))
        return server


async def _deny(client: asyncio.StreamWriter, denied: Denied, host: str) -> None:
    log.warning("egress.denied", host=host[:253], reason=denied.reason, status=denied.status)
    text = _STATUS_TEXT.get(denied.status, "Error")
    with contextlib.suppress(Exception):
        head = f"HTTP/1.1 {denied.status} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        client.write(head.encode())
        await client.drain()
        client.close()
