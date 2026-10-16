"""SSRF-safe HTTP(S) fetching (OWASP API7 / SSRF).

Two layers:

- ``check_url`` is syntactic and needs no DNS (also used by the API before queueing). It checks
  the scheme, credentials, port allow-list, IP literals, numeric IP encodings (``2130706433``,
  ``0x7f.1``, ``0177.1``), and ``localhost`` / internal-only names (including single-label names
  such as the compose service ``postgres``).
- ``SafeFetcher`` (egress worker only) resolves DNS **once**, refuses the host if **any**
  address is not public, then connects **to the validated IP** while sending the original
  ``Host`` header and TLS SNI. A second DNS answer (rebinding) is never consulted. Redirects
  are followed manually, each hop re-checked (max 3). Proxy environment variables are ignored.

Addresses count as public only if ``ipaddress`` says ``is_global`` and none of the explicit
ranges match. IPv4-mapped/compatible, NAT64, 6to4 and Teredo IPv6 forms are judged by the IPv4
they embed, and NAT64 prefixes are refused outright.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import idna

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from spoldzielnia_ai.core.settings import FetchSettings

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, incl. the 169.254.169.254 metadata endpoint
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "::1/128",
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "fec0::/10",
        "ff00::/8",
    )
)
_INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home.arpa",
    ".intranet",
    ".corp",
)
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]*|[0-9]+)$", re.IGNORECASE)


class SsrfBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FetchFailed(Exception):
    """The fetch was allowed but did not produce a usable document."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Target:
    scheme: str
    host: str  # ASCII (IDNA) hostname, or the IP literal without brackets
    port: int
    path: str  # path + query, never empty
    ip_literal: IPAddress | None

    @property
    def host_header(self) -> str:
        """``host[:port]``, the port only when it isn't the scheme's default."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = {"http": 80, "https": 443}[self.scheme]
        return host if self.port == default else f"{host}:{self.port}"

    @property
    def url(self) -> str:
        return urlunsplit((self.scheme, self.host_header, self.path, "", ""))


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    packed = ip.packed
    if packed[:12] == b"\x00" * 12 and packed[12:] != b"\x00\x00\x00\x00":  # IPv4-compatible
        return ipaddress.IPv4Address(packed[12:])
    return None


def is_public_ip(ip: IPAddress) -> bool:
    if any(ip in net for net in BLOCKED_NETWORKS if net.version == ip.version):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and (inner := _embedded_ipv4(ip)) is not None:
        return is_public_ip(inner)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


def check_url(url: str, settings: FetchSettings) -> Target:
    """Syntactic SSRF checks. Raises ``SsrfBlocked``. No DNS lookups."""
    if len(url) > settings.max_url_length:
        raise SsrfBlocked("url_too_long")
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url) or "\\" in url:
        raise SsrfBlocked("invalid_url")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SsrfBlocked("invalid_url") from None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "http") or (scheme == "http" and not settings.allow_http):
        raise SsrfBlocked("scheme_not_allowed")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise SsrfBlocked("credentials_in_url")
    raw_host = (parts.hostname or "").rstrip(".")
    if not raw_host:
        raise SsrfBlocked("invalid_url")
    port = port or (443 if scheme == "https" else 80)
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))

    try:
        ip = ipaddress.ip_address(raw_host)
    except ValueError:
        ip = None
    if ip is not None:
        if not is_public_ip(ip):
            raise SsrfBlocked("private_address")
        _check_port(port, settings)
        return Target(scheme, ip.compressed, port, path, ip)

    labels = raw_host.split(".")
    if all(_NUMERIC_LABEL.fullmatch(label) for label in labels):
        raise SsrfBlocked("ip_encoding")  # 2130706433, 0x7f.1, 127.1, 0177.0.0.1 …
    try:
        host = idna.encode(raw_host, uts46=True).decode("ascii").lower()
    except idna.IDNAError:
        raise SsrfBlocked("invalid_hostname") from None
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES) or "." not in host:
        raise SsrfBlocked("internal_hostname")
    _check_port(port, settings)
    return Target(scheme, host, port, path, None)


def _check_port(port: int, settings: FetchSettings) -> None:
    if port not in settings.allowed_ports:
        raise SsrfBlocked("port_not_allowed")


async def system_resolver(host: str, port: int) -> list[IPAddress]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[IPAddress] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = ipaddress.ip_address(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str  # final URL after redirects
    content_type: str  # media type without parameters
    charset: str | None
    body: bytes
    redirects: int


class SafeFetcher:
    def __init__(
        self,
        settings: FetchSettings,
        resolver: Callable[[str, int], Awaitable[list[IPAddress]]] = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._transport = transport

    async def _pin(self, target: Target) -> IPAddress:
        if target.ip_literal is not None:
            return target.ip_literal
        try:
            async with asyncio.timeout(self._settings.connect_timeout_seconds):
                addresses = await self._resolver(target.host, target.port)
        except (OSError, TimeoutError):
            raise FetchFailed("dns_failed") from None
        if not addresses:
            raise FetchFailed("dns_failed")
        if not all(is_public_ip(a) for a in addresses):
            raise SsrfBlocked("private_address")
        return addresses[0]

    async def fetch(self, url: str) -> FetchResult:
        timeout = httpx.Timeout(
            self._settings.total_timeout_seconds, connect=self._settings.connect_timeout_seconds
        )
        async with (
            httpx.AsyncClient(
                transport=self._transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,  # never route through proxies from the environment
                headers={
                    "User-Agent": self._settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.9",
                },
            ) as client,
            asyncio.timeout(self._settings.total_timeout_seconds),
        ):
            current = url
            for hop in range(self._settings.max_redirects + 1):
                target = check_url(current, self._settings)
                ip = await self._pin(target)
                pinned = Target(target.scheme, ip.compressed, target.port, target.path, ip)
                request = client.build_request(
                    "GET",
                    pinned.url,
                    headers={"Host": target.host_header},
                    extensions={"sni_hostname": target.host} if target.scheme == "https" else {},
                )
                try:
                    response = await client.send(request, stream=True)
                except httpx.HTTPError as exc:
                    raise FetchFailed(f"connection_failed:{type(exc).__name__}") from None
                try:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchFailed("redirect_without_location")
                        current = urljoin(target.url, location)
                        continue
                    return await self._read(response, target, hop)
                finally:
                    await response.aclose()
            raise FetchFailed("too_many_redirects")

    async def _read(self, response: httpx.Response, target: Target, hops: int) -> FetchResult:
        if response.status_code != 200:
            raise FetchFailed(f"http_{response.status_code}")
        media_type, _, params = response.headers.get("content-type", "").partition(";")
        media_type = media_type.strip().lower()
        if media_type not in self._settings.allowed_content_types:
            raise FetchFailed("content_type_not_allowed")
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._settings.max_body_bytes:
            raise FetchFailed("too_large")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._settings.max_body_bytes:
                raise FetchFailed("too_large")
        charset = None
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip("\"' ").lower()
        return FetchResult(target.url, media_type, charset, bytes(body), hops)
