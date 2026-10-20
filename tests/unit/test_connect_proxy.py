"""Chunk 7.3: the ``llm-egress`` CONNECT proxy (allowlist, public addresses only, limits)."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import TYPE_CHECKING

import pytest

from spoldzielnia_ai.net.connect_proxy import ConnectProxy, Denied, parse_connect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.net.safe_fetch import IPAddress

PUBLIC = ipaddress.ip_address("93.184.216.34")
DNS: dict[str, list[IPAddress]] = {
    "api.example.com": [PUBLIC],
    "rebind.example.com": [PUBLIC, ipaddress.ip_address("10.0.0.5")],
    "meta.example.com": [ipaddress.ip_address("169.254.169.254")],
    "mapped.example.com": [ipaddress.ip_address("::ffff:127.0.0.1")],
}
ALLOWED = frozenset(
    {"api.example.com", "rebind.example.com", "meta.example.com", "mapped.example.com"}
)


async def _resolve(host: str, _port: int) -> list[IPAddress]:
    return DNS[host]


class Upstream:
    """An echo server standing in for the cloud API; records where the proxy connected."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, int]] = []
        self.port = 0

    async def echo(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    async def open(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        self.opened.append((host, port))
        return await asyncio.open_connection("127.0.0.1", self.port)


@pytest.fixture
async def upstream() -> AsyncIterator[Upstream]:
    up = Upstream()
    server = await asyncio.start_server(up.echo, "127.0.0.1", 0)
    up.port = server.sockets[0].getsockname()[1]
    async with server:
        yield up


async def _proxy(upstream: Upstream, **options: float) -> tuple[ConnectProxy, int, asyncio.Server]:
    proxy = ConnectProxy(ALLOWED, resolver=_resolve, opener=upstream.open, **options)  # pyright: ignore[reportArgumentType]
    server = await proxy.serve("127.0.0.1", 0)
    return proxy, server.sockets[0].getsockname()[1], server


async def _request(
    port: int, head: bytes
) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    status = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    return status, reader, writer


async def test_an_allowed_host_gets_a_tunnel_to_its_validated_address(upstream: Upstream) -> None:
    _, port, server = await _proxy(upstream)
    async with server:
        status, reader, writer = await _request(
            port, b"CONNECT API.example.com.:443 HTTP/1.1\r\nHost: api.example.com:443\r\n\r\n"
        )
        assert status.startswith(b"HTTP/1.1 200")
        writer.write(b"\x16\x03\x01 tls bytes")
        await writer.drain()
        assert (
            await asyncio.wait_for(reader.readexactly(13), timeout=5) == b"\x16\x03\x01 tls bytes"
        )
        writer.close()
    assert upstream.opened == [(str(PUBLIC), 443)]


@pytest.mark.parametrize(
    ("head", "status"),
    [
        (b"CONNECT evil.example.org:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT api.example.com:80 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT api.example.com:22 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT rebind.example.com:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT meta.example.com:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT mapped.example.com:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT postgres:5432 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n", b"400"),
        (b"GET http://api.example.com/ HTTP/1.1\r\n\r\n", b"405"),
        (b"CONNECT api.example.com HTTP/1.1\r\n\r\n", b"400"),
        (b"\xff\xfe\r\n\r\n", b"400"),
        (b"CONNECT api.example.com:443 HTTP/1.1\r\n" + b"X: " + b"a" * 9000 + b"\r\n\r\n", b"400"),
    ],
)
async def test_everything_else_is_refused(upstream: Upstream, head: bytes, status: bytes) -> None:
    _, port, server = await _proxy(upstream)
    async with server:
        response, _, writer = await _request(port, head)
        writer.close()
    assert response.split(b" ")[1] == status
    assert upstream.opened == []


async def test_a_slow_request_head_times_out(upstream: Upstream) -> None:
    _, port, server = await _proxy(upstream, header_timeout=0.2)
    async with server:
        response, _, writer = await _request(port, b"CONNECT api.example.com:443 HTTP/1.1\r\n")
        writer.close()
    assert response.startswith(b"HTTP/1.1 400")


async def test_unreachable_upstreams_are_bad_gateways(upstream: Upstream) -> None:
    async def refuse(_host: str, _port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        raise ConnectionRefusedError

    proxy = ConnectProxy(ALLOWED, resolver=_resolve, opener=refuse)
    server = await proxy.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        response, _, writer = await _request(port, b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
        writer.close()
    assert response.startswith(b"HTTP/1.1 502")


async def test_concurrent_tunnels_are_limited(upstream: Upstream) -> None:
    proxy = ConnectProxy(ALLOWED, resolver=_resolve, opener=upstream.open, max_connections=1)
    server = await proxy.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        first, _, keep = await _request(port, b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
        second, _, other = await _request(port, b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
        keep.close()
        other.close()
    assert first.startswith(b"HTTP/1.1 200")
    assert second.startswith(b"HTTP/1.1 503")


def test_parse_connect() -> None:
    assert parse_connect(b"CONNECT Api.Example.com:443 HTTP/1.1\r\n\r\n") == (
        "api.example.com",
        443,
    )
    for head, code in (
        (b"CONNECT a:443\r\n\r\n", 400),
        (b"POST a:443 HTTP/1.1\r\n\r\n", 405),
        (b"CONNECT a:x HTTP/1.1\r\n\r\n", 400),
        (b"CONNECT :443 HTTP/1.1\r\n\r\n", 400),
    ):
        with pytest.raises(Denied) as exc:
            parse_connect(head)
        assert exc.value.status == code
