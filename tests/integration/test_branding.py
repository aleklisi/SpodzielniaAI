"""Chunks 13.1-13.3: organisation branding (US-07). The service, its files, the API and the
database checks of migration 0027."""

from __future__ import annotations

import io
from typing import IO, TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, PngImagePlugin
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.api import branding as branding_api
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.db.models import SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.files.antivirus import ScannerUnavailable, ScanResult
from tests.fixtures.app import SITE, Harness, LoggedIn, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings

pytestmark = pytest.mark.integration


class Scanner:
    def __init__(self, verdict: ScanResult | None = None) -> None:
        self.verdict = verdict or ScanResult(clean=True)

    async def scan(self, src: IO[bytes]) -> ScanResult:
        src.read()
        return self.verdict


class DownScanner:
    async def scan(self, src: IO[bytes]) -> ScanResult:
        raise ScannerUnavailable("clamd down")


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


async def _admin(h: Harness, **claims: Any) -> LoggedIn:
    return await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd", "otp"], **claims)


def _png(size: tuple[int, int] = (800, 200), *, text_chunk: bool = False) -> bytes:
    image = Image.new("RGBA", size, (31, 111, 74, 255))
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    if text_chunk:
        info.add_text("Author", "Jan Kowalski, tel. 600 100 200")
    image.save(out, "PNG", pnginfo=info)
    return out.getvalue()


def _jpeg_with_exif() -> bytes:
    image = Image.new("RGB", (300, 300), (200, 80, 20))
    exif = Image.Exif()
    exif[0x010F] = "Telefon Jana"  # Make
    exif[0x8298] = "Pasieka Kowalskich"  # Copyright
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


async def _upload(admin: LoggedIn, name: str, data: bytes, content_type: str) -> Any:
    return await admin.client.post(
        "/admin/branding/logo", files={"file": (name, data, content_type)}, headers=admin.headers
    )


async def _events(factory: SessionFactory) -> list[tuple[str, dict[str, Any]]]:
    async with factory() as session:
        rows = await session.scalars(
            select(SecurityAuditLog)
            .where(
                SecurityAuditLog.event_type.in_(
                    [EventType.BRANDING_CHANGED.value, EventType.BRANDING_RESET.value]
                )
            )
            .order_by(SecurityAuditLog.id)
        )
        return [(r.event_type, dict(r.payload)) for r in rows]


# --- reading ------------------------------------------------------------------------------------


async def test_everyone_reads_the_default_branding(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        async with AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE) as anonymous:
            response = await anonymous.get("/branding")
        assert response.status_code == 200
        assert response.json() == {
            "org_name": "Spółdzielnia AI",
            "custom_name": False,
            "version": 0,
            "palette": None,
            "logo_url": None,
            "favicon_urls": None,
        }


# --- access -------------------------------------------------------------------------------------


async def test_only_an_admin_with_mfa_changes_branding(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        admin_without_mfa = await h.login(roles=["ADMIN", "MEMBER"], amr=["pwd"])
        body = {"org_name": "Ktoś inny"}
        for who in (member, admin_without_mfa):
            assert (await who.client.get("/admin/branding")).status_code == 403
            response = await who.client.put("/admin/branding", json=body, headers=who.headers)
            assert response.status_code == 403
        assert (await admin_without_mfa.client.get("/admin/branding")).json()["detail"][
            "error"
        ] == "mfa_required"

        admin = await _admin(h)
        no_csrf = await admin.client.put("/admin/branding", json=body, headers={"Origin": SITE})
        assert no_csrf.status_code == 403
        assert (await admin.client.get("/branding")).json()["custom_name"] is False


# --- name and colours ---------------------------------------------------------------------------


async def test_an_admin_sets_the_name_and_colours(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        admin = await _admin(h)
        response = await admin.client.put(
            "/admin/branding",
            json={"org_name": "  Pasieka Kowalscy ", "brand": "#1F6F4A", "accent": "#c2410c"},
            headers=admin.headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["org_name"] == "Pasieka Kowalscy"
        assert body["brand"] == "#1f6f4a"
        assert body["version"] == 1
        assert body["palette"]["light"]["primary"] == "#1f6f4a"
        assert set(body["palette"]) == {"light", "dark"}

        member = await h.login()
        public = (await member.client.get("/branding")).json()
        assert public["org_name"] == "Pasieka Kowalscy"
        assert public["palette"] == body["palette"]
        assert "brand" not in public  # the public view carries only what the page needs

        # Saving the same again changes nothing.
        again = await admin.client.put(
            "/admin/branding",
            json={"org_name": "Pasieka Kowalscy", "brand": "#1f6f4a", "accent": "#c2410c"},
            headers=admin.headers,
        )
        assert again.json()["version"] == 1
        events = await _events(factory)
        assert events == [
            (
                "BRANDING_CHANGED",
                {"fields": ["org_name", "colours"], "version": 1, "adjusted": ["accent"]},
            )
        ]


async def test_unreadable_or_malformed_values_are_refused(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        admin = await _admin(h)
        cases = [
            ({"brand": "#1f6f4a"}, "colours_together"),
            ({"brand": "red", "accent": "#000000"}, "invalid_colour"),
            (
                {"brand": "#1f6f4a;}body{display:none", "accent": "#000000"},
                None,
            ),  # too long: 422 by pydantic
            ({"org_name": "x" * 61}, "name_too_long"),
            ({"org_name": "Pasieka‮akeisaP"}, "name_invalid"),  # a bidi override
            ({"org_name": "Pasieka\nKowalscy"}, "name_invalid"),
        ]
        for body, code in cases:
            response = await admin.client.put("/admin/branding", json=body, headers=admin.headers)
            assert response.status_code == 422, body
            if code:
                assert response.json()["detail"]["error"] == code
        assert (await admin.client.get("/branding")).json()["version"] == 0


async def test_the_preview_shows_adjustments_and_ratios_without_saving(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        admin = await _admin(h)
        response = await admin.client.post(
            "/admin/branding/preview",
            json={"brand": "#fff59d", "accent": "#6b4f96"},
            headers=admin.headers,
        )
        assert response.status_code == 200
        preview = response.json()
        assert preview["adjusted"] == ["brand"]
        assert preview["light"]["primary"] != "#fff59d"
        assert all(c["ratio"] >= c["required"] for c in preview["checks"])
        assert {c["mode"] for c in preview["checks"]} == {"light", "dark"}
        assert (await admin.client.get("/branding")).json()["version"] == 0


# --- logo -------------------------------------------------------------------------------------


async def test_a_logo_is_re_encoded_into_versioned_files(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        h.app.state.scanner = Scanner()
        admin = await _admin(h)
        response = await _upload(admin, "logo.png", _png(text_chunk=True), "image/png")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["logo_url"] == "/api/branding/files/1/logo.png"
        assert body["favicon_urls"] == {
            "32": "/api/branding/files/1/favicon-32.png",
            "180": "/api/branding/files/1/favicon-180.png",
        }

        async with AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE) as anonymous:
            logo = await anonymous.get("/branding/files/1/logo.png")
            assert logo.status_code == 200
            assert logo.headers["content-type"] == "image/png"
            assert logo.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert logo.headers["x-content-type-options"] == "nosniff"
            with Image.open(io.BytesIO(logo.content)) as image:
                assert image.size == (512, 128)  # fits 512x128, aspect kept
                assert image.mode == "RGBA"
                assert not image.info.get("Author")
                assert "icc_profile" not in image.info
            for side in (32, 180):
                favicon = await anonymous.get(f"/branding/files/1/favicon-{side}.png")
                with Image.open(io.BytesIO(favicon.content)) as image:
                    assert image.size == (side, side)
            assert (await anonymous.get("/branding/files/1/original.png")).status_code == 404
            assert (await anonymous.get("/branding/files/1/..%2Fsecret")).status_code == 404

        # A new logo gets a new URL; the old files are gone.
        second = await _upload(admin, "logo.jpg", _jpeg_with_exif(), "image/jpeg")
        assert second.json()["logo_url"] == "/api/branding/files/2/logo.png"
        assert (await admin.client.get("/branding/files/1/logo.png")).status_code == 404
        assert not [k for k in h.storage.objects if k.startswith("branding/1/")]
        with Image.open(
            io.BytesIO((await admin.client.get("/branding/files/2/logo.png")).content)
        ) as image:
            assert image.size == (128, 128)
            assert not image.getexif()

        removed = await admin.client.delete("/admin/branding/logo", headers=admin.headers)
        assert removed.json()["logo_url"] is None
        assert not [k for k in h.storage.objects if k.startswith("branding/")]
        events = await _events(factory)
        assert [e[1]["fields"] for e in events] == [["logo"], ["logo"], ["logo"]]
        assert all("logo_sha256" in e[1] for e in events[:2])


@pytest.mark.parametrize(
    ("name", "data", "content_type", "status", "code"),
    [
        (
            "logo.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "image/svg+xml",
            415,
            "type_not_allowed",
        ),
        ("logo.gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif", 415, None),
        ("logo.png", b"MZ\x90\x00" + bytes(512), "image/png", 415, "executable"),
        ("logo.png", b"\x89PNG\r\n\x1a\n" + bytes(3 * 1024 * 1024), "image/png", 413, "too_large"),
        ("logo.png", _png((5000, 10)), "image/png", 422, "dimensions"),
        ("logo.heic", b"\x00\x00\x00\x18ftypheic" + bytes(64), "image/heic", 415, None),
    ],
)
async def test_anything_but_a_plain_image_is_refused(
    settings: Settings,
    factory: SessionFactory,
    name: str,
    data: bytes,
    content_type: str,
    status: int,
    code: str | None,
) -> None:
    async with harness(settings, factory) as h:
        h.app.state.scanner = Scanner()
        admin = await _admin(h)
        response = await _upload(admin, name, data, content_type)
        assert response.status_code == status, response.text
        if code:
            assert response.json()["detail"]["error"] == code
        assert not h.storage.objects
        assert (await admin.client.get("/branding")).json()["logo_url"] is None


async def test_an_infected_logo_is_refused_and_a_down_scanner_fails_closed(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        h.app.state.scanner = Scanner(ScanResult(clean=False, signature="Eicar-Test-Signature"))
        admin = await _admin(h)
        infected = await _upload(admin, "logo.png", _png(), "image/png")
        assert (infected.status_code, infected.json()["detail"]["error"]) == (422, "infected")
        h.app.state.scanner = DownScanner()
        down = await _upload(admin, "logo.png", _png(), "image/png")
        assert (down.status_code, down.json()["detail"]["error"]) == (503, "scanner_unavailable")
        assert not h.storage.objects


# --- reset, limits, database ------------------------------------------------------------------


async def test_reset_returns_to_the_default_look(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        h.app.state.scanner = Scanner()
        admin = await _admin(h)
        await admin.client.put(
            "/admin/branding",
            json={"org_name": "Pasieka", "brand": "#1f6f4a", "accent": "#c2410c"},
            headers=admin.headers,
        )
        await _upload(admin, "logo.png", _png(), "image/png")
        response = await admin.client.post("/admin/branding/reset", headers=admin.headers)
        body = response.json()
        assert body["org_name"] == "Spółdzielnia AI"
        assert (body["palette"], body["logo_url"], body["custom_name"]) == (None, None, False)
        assert body["version"] == 3  # still growing: caches see the change
        assert not h.storage.objects
        assert (await _events(factory))[-1] == ("BRANDING_RESET", {"version": 3})


async def test_writes_are_rate_limited_per_admin(
    settings: Settings, factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(branding_api, "WRITES_PER_HOUR", 2)
    async with harness(settings, factory) as h:
        admin = await _admin(h)
        statuses = [
            (
                await admin.client.put(
                    "/admin/branding", json={"org_name": f"P{i}"}, headers=admin.headers
                )
            ).status_code
            for i in range(3)
        ]
        assert statuses == [200, 200, 429]


@pytest.mark.parametrize(
    "assignment",
    [
        "brand_hex = 'red', accent_hex = '#000000', palette = '{\"light\": {}, \"dark\": {}}'",
        "brand_hex = '#000000', accent_hex = NULL",
        "brand_hex = '#000000', accent_hex = '#000000',"
        ' palette = \'{"light": {"primary": "#000;}body{x:y"}, "dark": {}}\'',
        "brand_hex = '#000000', accent_hex = '#000000',"
        ' palette = \'{"light": {"primary": 1}, "dark": {}}\'',
        "brand_hex = '#000000', accent_hex = '#000000', palette = '{\"light\": {}}'",
        "org_name = ''",
        "org_name = E'Pasieka\\nKowalscy'",
        "logo_version = 3",
    ],
)
async def test_the_database_refuses_what_could_reach_css(
    factory: SessionFactory, assignment: str
) -> None:
    async with factory() as session, session.begin():
        await session.execute(text("INSERT INTO branding (id) VALUES (1)"))
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            await session.execute(text(f"UPDATE branding SET {assignment} WHERE id = 1"))  # noqa: S608 - test constants
    with pytest.raises(IntegrityError):
        async with factory() as session, session.begin():
            await session.execute(text("INSERT INTO branding (id) VALUES (2)"))
