"""Chunk 2.4: SSRF protection (PLAN Etap 2 verification + DNS rebinding + IP pinning)."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from spoldzielnia_ai.core.settings import FetchSettings
from spoldzielnia_ai.net.safe_fetch import (
    FetchFailed,
    FetchResult,
    SafeFetcher,
    SsrfBlocked,
    check_url,
    is_public_ip,
)
from spoldzielnia_ai.net.to_markdown import to_markdown

if TYPE_CHECKING:
    from collections.abc import Iterator

    from spoldzielnia_ai.net.safe_fetch import IPAddress

SETTINGS = FetchSettings(allow_http=True)
PSZCZOLKI = "pszczółki.pl".encode("idna").decode()  # IDNA 2003 and UTS46 agree for this name
PUBLIC = ipaddress.ip_address("93.184.216.34")
PUBLIC_2 = ipaddress.ip_address("151.101.1.69")
PRIVATE = ipaddress.ip_address("10.0.0.5")
HTML = (
    "<html><body><nav>menu</nav><article><h1>Warroza</h1>"
    "<p>Kwas szczawiowy stosujemy zimą, gdy w ulu nie ma czerwiu. To skuteczna metoda.</p>"
    "</article><script>alert(1)</script></body></html>"
)


# --- Syntactic checks (also run by the API) -----------------------------------------------


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1:8000", "private_address"),
        ("http://169.254.169.254/latest/meta-data", "private_address"),
        ("http://[::1]", "private_address"),
        ("http://[::ffff:127.0.0.1]", "private_address"),
        ("http://[::ffff:7f00:1]/", "private_address"),
        ("http://[64:ff9b::a9fe:a9fe]/", "private_address"),  # NAT64 → 169.254.169.254
        ("http://[2002:7f00:1::]/", "private_address"),  # 6to4 embedding 127.0.0.1
        ("http://[fe80::1]/", "private_address"),
        ("http://[fd00::1]/", "private_address"),
        ("http://2130706433", "ip_encoding"),
        ("http://0x7f.1", "ip_encoding"),
        ("http://0177.0.0.1", "ip_encoding"),
        ("http://127.1", "ip_encoding"),
        ("http://localhost", "internal_hostname"),
        ("http://LOCALHOST./", "internal_hostname"),
        ("http://api.localhost", "internal_hostname"),
        ("http://postgres:5432", "internal_hostname"),
        ("https://example.org:5432/", "port_not_allowed"),
        ("http://postgres", "internal_hostname"),  # single label: a compose service name
        ("http://printer.local", "internal_hostname"),
        ("http://0.0.0.0", "private_address"),
        ("http://100.64.0.1", "private_address"),  # CGNAT
        ("http://192.168.1.1", "private_address"),
        ("http://172.30.2.10", "private_address"),  # the compose backend network
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://example.org", "scheme_not_allowed"),
        ("ftp://example.org", "scheme_not_allowed"),
        ("data:text/html,<script>", "scheme_not_allowed"),
        ("https://user:pass@example.org", "credentials_in_url"),
        ("https://example.org@127.0.0.1", "credentials_in_url"),
        ("https://example.org:22/", "port_not_allowed"),
        ("https://exa mple.org/", "invalid_url"),
        ("https://example.org\\@evil", "invalid_url"),
        ("https:///path", "invalid_url"),
    ],
)
def test_blocked_urls(url: str, reason: str) -> None:
    with pytest.raises(SsrfBlocked) as excinfo:
        check_url(url, SETTINGS)
    assert excinfo.value.reason == reason


def test_http_needs_opt_in() -> None:
    with pytest.raises(SsrfBlocked, match="scheme_not_allowed"):
        check_url("http://example.org", FetchSettings())
    assert check_url("https://example.org", FetchSettings()).scheme == "https"


@pytest.mark.parametrize(
    ("url", "host", "normalised"),
    [
        ("https://example.org", "example.org", "https://example.org/"),
        ("https://Example.ORG/a?b=1#frag", "example.org", "https://example.org/a?b=1"),
        ("https://pszczółki.pl/miód", PSZCZOLKI, f"https://{PSZCZOLKI}/mi%C3%B3d"),
        ("https://93.184.216.34/x", "93.184.216.34", "https://93.184.216.34/x"),
        ("https://example.org:8443/", "example.org", "https://example.org:8443/"),
        ("https://bad.cafe/", "bad.cafe", "https://bad.cafe/"),  # hex-looking labels are names
    ],
)
def test_allowed_urls_are_normalised(url: str, host: str, normalised: str) -> None:
    target = check_url(url, SETTINGS)
    assert target.host == host
    assert target.url.split("/", 3)[:3] == normalised.split("/", 3)[:3]


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700:4700::1111", True),
        ("::ffff:93.184.216.34", True),
        ("10.1.2.3", False),
        ("::ffff:10.1.2.3", False),
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),  # Teredo: 2001::/23 is not global
        ("2001:db8::1", False),
        ("ff02::1", False),
    ],
)
def test_is_public_ip(address: str, public: bool) -> None:
    assert is_public_ip(ipaddress.ip_address(address)) is public


# --- Fetching (DNS validation, pinning, redirects) ---------------------------------------


class Resolver:
    def __init__(self, *answers: list[IPAddress]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    async def __call__(self, host: str, port: int) -> list[IPAddress]:
        self.calls.append(host)
        return self.answers[min(len(self.calls), len(self.answers)) - 1]


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


async def test_host_resolving_to_private_ip_is_blocked(router: respx.MockRouter) -> None:
    route = router.get(url__regex=r".*")
    fetcher = SafeFetcher(SETTINGS, Resolver([PRIVATE]))
    with pytest.raises(SsrfBlocked, match="private_address"):
        await fetcher.fetch("https://innocent.example.org/")
    assert not route.called


async def test_any_private_answer_blocks_the_host(router: respx.MockRouter) -> None:
    fetcher = SafeFetcher(SETTINGS, Resolver([PUBLIC, PRIVATE]))
    with pytest.raises(SsrfBlocked, match="private_address"):
        await fetcher.fetch("https://mixed.example.org/")


async def test_connection_is_pinned_to_validated_ip(router: respx.MockRouter) -> None:
    """DNS rebinding: the second lookup would say 10.0.0.5. It is never made, and the request
    goes to the address validated first, with the original Host and SNI."""
    route = router.get(f"https://{PUBLIC}/page").mock(return_value=httpx.Response(200, html=HTML))
    resolver = Resolver([PUBLIC], [PRIVATE])
    result = await SafeFetcher(SETTINGS, resolver).fetch("https://rebind.example.org/page")

    assert resolver.calls == ["rebind.example.org"]
    request = route.calls.last.request
    assert request.url.host == str(PUBLIC)
    assert request.headers["host"] == "rebind.example.org"
    assert request.extensions["sni_hostname"] == "rebind.example.org"
    assert result.url == "https://rebind.example.org/page"
    assert result.content_type == "text/html"


async def test_redirect_to_metadata_is_blocked_at_hop_two(router: respx.MockRouter) -> None:
    router.get(f"https://{PUBLIC}/start").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})
    )
    metadata = router.get(url__regex=r"http://169\.254\.169\.254.*")
    with pytest.raises(SsrfBlocked, match="private_address"):
        await SafeFetcher(SETTINGS, Resolver([PUBLIC])).fetch("https://public.example.org/start")
    assert not metadata.called


async def test_redirect_to_name_resolving_privately_is_blocked(router: respx.MockRouter) -> None:
    router.get(f"https://{PUBLIC}/a").mock(
        return_value=httpx.Response(301, headers={"Location": "https://internal.example.org/"})
    )
    resolver = Resolver([PUBLIC], [PRIVATE])
    with pytest.raises(SsrfBlocked):
        await SafeFetcher(SETTINGS, resolver).fetch("https://public.example.org/a")
    assert resolver.calls == ["public.example.org", "internal.example.org"]  # re-resolved per hop


async def test_relative_redirects_are_followed(router: respx.MockRouter) -> None:
    router.get(f"https://{PUBLIC}/old").mock(
        return_value=httpx.Response(301, headers={"Location": "/new"})
    )
    router.get(f"https://{PUBLIC}/new").mock(return_value=httpx.Response(200, text="ok"))
    result = await SafeFetcher(SETTINGS, Resolver([PUBLIC])).fetch("https://site.example.org/old")
    assert result.url == "https://site.example.org/new"
    assert result.redirects == 1


async def test_too_many_redirects(router: respx.MockRouter) -> None:
    router.get(url__regex=rf"https://{PUBLIC}/.*").mock(
        return_value=httpx.Response(302, headers={"Location": "/again"})
    )
    with pytest.raises(FetchFailed, match="too_many_redirects"):
        await SafeFetcher(SETTINGS, Resolver([PUBLIC])).fetch("https://loop.example.org/x")


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            httpx.Response(
                200, content=b"x" * (5 * 1024 * 1024 + 1), headers={"content-type": "text/plain"}
            ),
            "too_large",
        ),
        (
            httpx.Response(
                200, content=b"\x7fELF", headers={"content-type": "application/octet-stream"}
            ),
            "content_type_not_allowed",
        ),
        (httpx.Response(404, text="nope"), "http_404"),
    ],
    ids=["body-limit", "content-type", "status"],
)
async def test_response_limits(
    router: respx.MockRouter, response: httpx.Response, reason: str
) -> None:
    router.get(f"https://{PUBLIC}/f").mock(return_value=response)
    with pytest.raises(FetchFailed, match=reason):
        await SafeFetcher(SETTINGS, Resolver([PUBLIC])).fetch("https://files.example.org/f")


async def test_ipv6_target_is_bracketed(router: respx.MockRouter) -> None:
    v6 = ipaddress.ip_address("2606:4700:4700::1111")
    route = router.get(f"https://[{v6}]/").mock(return_value=httpx.Response(200, text="ok"))
    await SafeFetcher(SETTINGS, Resolver([v6])).fetch("https://v6.example.org/")
    assert route.called


async def test_proxy_environment_is_ignored(
    router: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    route = router.get(f"https://{PUBLIC}/").mock(return_value=httpx.Response(200, text="ok"))
    await SafeFetcher(SETTINGS, Resolver([PUBLIC])).fetch("https://site.example.org/")
    assert route.calls.last.request.url.host == str(PUBLIC)


# --- Markdown conversion ------------------------------------------------------------------


def test_html_is_reduced_to_main_content() -> None:
    markdown = to_markdown(
        FetchResult("https://x.example.org/", "text/html", "utf-8", HTML.encode(), 0)
    )
    assert "Kwas szczawiowy" in markdown
    assert "alert(1)" not in markdown


def test_plain_text_uses_declared_charset() -> None:
    body = "Zażółć".encode("iso-8859-2")
    result = FetchResult("https://x.example.org/", "text/plain", "iso-8859-2", body, 0)
    assert to_markdown(result) == "Zażółć"
