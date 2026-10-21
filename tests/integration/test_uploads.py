"""Chunk 2.3: ``POST /notes/{id}/attachments`` end to end.

Real Postgres (RLS), fake scanner, in-memory object storage.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.files.antivirus import ScannerUnavailable, ScanResult
from tests.fixtures import files
from tests.fixtures.app import Harness, LoggedIn, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from httpx import Response

    from spoldzielnia_ai.core.settings import Settings
    from tests.integration.conftest import PgInstance

pytestmark = pytest.mark.integration

EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
GPS_IFD = 0x8825


@dataclass
class FakeScanner:
    down: bool = False
    scanned: int = 0

    async def scan(self, src: IO[bytes]) -> ScanResult:
        if self.down:
            raise ScannerUnavailable("down")
        self.scanned += 1
        data = src.read()
        if EICAR in data:
            return ScanResult(clean=False, signature="Eicar-Test-Signature")
        return ScanResult(clean=True)


@pytest.fixture
async def factory(migrated: PgInstance) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(migrated.app_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def scanner() -> FakeScanner:
    return FakeScanner()


@pytest.fixture
async def h(
    settings: Settings, factory: SessionFactory, scanner: FakeScanner
) -> AsyncIterator[Harness]:
    async with harness(settings, factory) as h:
        h.app.state.scanner = scanner
        yield h


async def _note(user: LoggedIn) -> str:
    response = await user.client.post("/notes", json={"title": "Pasieka"}, headers=user.headers)
    return response.json()["id"]


async def _upload(
    user: LoggedIn, note_id: str, data: bytes, filename: str, ctype: str, **form: str
) -> Response:
    return await user.client.post(
        f"/notes/{note_id}/attachments",
        files={"file": (filename, data, ctype)},
        data=form,
        headers=user.headers,
    )


async def _audit(factory: SessionFactory, user_id: str, event: str) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = await session.execute(
            text(
                "SELECT payload FROM security_audit_log WHERE actor_id = :id AND event_type = :e "
                "ORDER BY id"
            ),
            {"id": user_id, "e": event},
        )
        return [row.payload for row in rows]


def _decrypt_object(
    h: Harness,
    attachment: dict[str, Any],
    owner: str,
    dek_wrapped: bytes,
    object_key: str | None = None,
) -> bytes:
    """The stored original (images also have a preview object since 9.2: pass its key)."""
    cipher = h.app.state.cipher
    aad = row_aad("note_attachment", UUID(attachment["id"]), UUID(owner))
    dek = cipher.unwrap(dek_wrapped, aad)
    if object_key is None:
        (blob,) = h.storage.objects.values()
    else:
        blob = h.storage.objects[object_key]
    return b"".join(cipher.decrypt_stream(dek, io.BytesIO(blob), aad))


async def test_pdf_upload_is_stored_encrypted(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    pdf = files.pdf(author="Jan Kowalski")

    response = await _upload(alice, note_id, pdf, "raport roczny.pdf", "application/pdf")

    assert response.status_code == 202, response.text
    attachment = response.json()
    assert attachment["filename"] == "raport roczny.pdf"
    assert attachment["mime_type"] == "application/pdf"
    assert attachment["scan_status"] == "CLEAN"
    ((key, blob),) = h.storage.objects.items()
    assert key.startswith("att/")
    assert b"%PDF" not in blob  # client-side encrypted

    async with rls_session(factory, UUID(alice.user_id)) as session:
        row = (
            await session.execute(
                text("SELECT dek_wrapped, filename_ciphertext FROM note_attachment WHERE id = :id"),
                {"id": attachment["id"]},
            )
        ).one()
    assert row is not None
    stored = _decrypt_object(h, attachment, alice.user_id, row.dek_wrapped)
    assert stored.startswith(b"%PDF")
    assert b"Kowalski" not in stored  # metadata stripped before storage
    assert b"raport" not in row.filename_ciphertext

    note = (await alice.client.get(f"/notes/{note_id}")).json()
    assert [a["id"] for a in note["attachments"]] == [attachment["id"]]
    status = (await alice.client.get(f"/notes/{note_id}/attachments/{attachment['id']}")).json()
    assert status["attachment"]["id"] == attachment["id"]
    assert status["derivatives"] == []


async def test_exe_renamed_pdf_is_415_and_audited(h: Harness, factory: SessionFactory) -> None:
    """PLAN Etap 2 verification."""
    alice = await h.login()
    note_id = await _note(alice)
    response = await _upload(
        alice, note_id, files.pe_executable(), "invoice.pdf", "application/pdf"
    )
    assert response.status_code == 415
    assert response.json()["detail"]["error"] == "executable"
    assert h.storage.objects == {}
    (event,) = await _audit(factory, alice.user_id, "UPLOAD_REJECTED")
    assert event["reason"] == "executable"
    assert "invoice" not in str(event)  # no filenames in the audit log


async def test_eicar_is_422_and_audited(h: Harness, factory: SessionFactory) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    response = await _upload(alice, note_id, EICAR, "test.txt", "text/plain")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "infected"
    assert h.storage.objects == {}
    (event,) = await _audit(factory, alice.user_id, "UPLOAD_REJECTED")
    assert event["signature"] == "Eicar-Test-Signature"


async def test_scanner_down_is_503_and_nothing_stored(h: Harness, scanner: FakeScanner) -> None:
    scanner.down = True
    alice = await h.login()
    note_id = await _note(alice)
    response = await _upload(alice, note_id, b"tresc", "a.txt", "text/plain")
    assert response.status_code == 503
    assert h.storage.objects == {}
    assert (await alice.client.get(f"/notes/{note_id}")).json()["attachments"] == []


async def test_zip_bomb_is_422(h: Harness, scanner: FakeScanner) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    response = await _upload(alice, note_id, files.high_ratio_zip(), "a.zip", "application/zip")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "archive_ratio"
    assert scanner.scanned == 0  # rejected before the scanner


async def test_oversized_upload_is_413_while_streaming(h: Harness, settings: Settings) -> None:
    h.app.state.settings = settings.model_copy(
        update={"uploads": settings.uploads.model_copy(update={"max_bytes": 1024})}
    )
    alice = await h.login()
    note_id = await _note(alice)
    response = await _upload(alice, note_id, b"a" * 4096, "a.txt", "text/plain")
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "too_large"


async def test_upload_to_other_users_note_is_404(h: Harness, scanner: FakeScanner) -> None:
    alice, bob = await h.login(), await h.login()
    note_id = await _note(alice)
    response = await _upload(bob, note_id, b"tresc", "a.txt", "text/plain")
    assert response.status_code == 404
    assert scanner.scanned == 0
    assert h.storage.objects == {}


def _gps_jpeg() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Pszczoła Cam"
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[2] = "N", (52.0, 13.0, 0.0)
    return files.jpeg(exif)


@pytest.mark.parametrize("keep", [False, True], ids=["strip", "keep-location"])
async def test_gps_exif(h: Harness, factory: SessionFactory, keep: bool) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    form = {"keep_location": "true"} if keep else {}
    response = await _upload(alice, note_id, _gps_jpeg(), "ul.jpg", "image/jpeg", **form)
    assert response.status_code == 202, response.text
    attachment = response.json()
    assert attachment["keep_location"] is keep

    async with rls_session(factory, UUID(alice.user_id)) as session:
        dek_wrapped, object_key = (
            await session.execute(
                text("SELECT dek_wrapped, object_key FROM note_attachment WHERE id = :id"),
                {"id": attachment["id"]},
            )
        ).one()
    stored = _decrypt_object(h, attachment, alice.user_id, dek_wrapped, object_key)
    with Image.open(io.BytesIO(stored)) as image:
        exif = image.getexif()
    assert bool(exif.get_ifd(GPS_IFD)) is keep
    assert 0x010F not in exif  # camera make always stripped
    retained = await _audit(factory, alice.user_id, "ATTACHMENT_LOCATION_RETAINED")
    assert len(retained) == (1 if keep else 0)


async def test_deleting_note_removes_objects(h: Harness) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    await _upload(alice, note_id, b"tresc", "a.txt", "text/plain")
    assert len(h.storage.objects) == 1
    assert (
        await alice.client.delete(f"/notes/{note_id}", headers=alice.headers)
    ).status_code == 204
    assert h.storage.objects == {}


async def test_upload_needs_csrf(h: Harness) -> None:
    alice = await h.login()
    note_id = await _note(alice)
    response = await alice.client.post(
        f"/notes/{note_id}/attachments", files={"file": ("a.txt", b"x", "text/plain")}
    )
    assert response.status_code == 403
