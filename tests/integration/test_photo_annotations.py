"""Chunk 9.2: private photo annotations and image serving, with the real app and Postgres.

A1: photos, annotations and image bytes are the owner's only (RLS, 404 for anyone else).
A2: image bytes are served as inert images. A11: boxes are validated server-side.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.enums import DerivativeKind
from spoldzielnia_ai.db.models import (
    AnnotatedImage,
    ImageAnnotation,
    NoteAttachment,
    NoteDerivative,
)
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from spoldzielnia_ai.files.antivirus import ScanResult
from spoldzielnia_ai.notebook.uploads import NoProcessing
from tests.fixtures import files
from tests.fixtures.app import harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.workers.jobs import DerivativeJob
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

MFA: dict[str, Any] = {"amr": ["pwd", "otp"]}
GPS_IFD = 0x8825


class CleanScanner:
    async def scan(self, src: IO[bytes]) -> ScanResult:
        src.read()
        return ScanResult(clean=True)


@dataclass
class RecordingQueue(NoProcessing):
    saved: list[DerivativeJob] = field(default_factory=list["DerivativeJob"])

    async def annotations_saved(self, job: DerivativeJob) -> None:
        self.saved.append(job)


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
async def h(
    settings: Settings, factory: SessionFactory, queue: RecordingQueue
) -> AsyncIterator[Harness]:
    async with harness(settings, factory) as h:
        h.app.state.scanner = CleanScanner()
        h.app.state.processing_queue = queue
        yield h


def big_jpeg(width: int = 3000, height: int = 2000, *, gps: bool = True) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Camera Maker"  # Make
    if gps:
        exif[GPS_IFD] = {1: "N", 2: (52.0, 13.0, 0.0)}
    out = io.BytesIO()
    Image.new("RGB", (width, height), "orange").save(out, "JPEG", exif=exif)
    return out.getvalue()


@dataclass
class Vocabulary:
    domain: str
    queen: str
    cell: str
    other_domain: str
    foreign: str


async def _vocabulary(h: Harness) -> Vocabulary:
    admin = await h.login(roles=["MEMBER", "ADMIN"], **MFA)

    async def post(path: str, body: dict[str, Any]) -> str:
        response = await admin.client.post(path, json=body, headers=admin.headers)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    bees = await post("/admin/atlas/domains", {"name": "Pszczelarstwo"})
    garden = await post("/admin/atlas/domains", {"name": "Ogrodnictwo"})
    return Vocabulary(
        bees,
        await post("/atlas/curation/labels", {"domain_id": bees, "name": "Matka pszczela"}),
        await post("/atlas/curation/labels", {"domain_id": bees, "name": "Matecznik"}),
        garden,
        await post("/atlas/curation/labels", {"domain_id": garden, "name": "Mszyca"}),
    )


async def _photo(user: LoggedIn, data: bytes | None = None, **form: str) -> tuple[str, str]:
    note = await user.client.post("/notes", json={"title": "Pasieka"}, headers=user.headers)
    note_id = note.json()["id"]
    response = await user.client.post(
        f"/notes/{note_id}/attachments",
        files={"file": ("ul.jpg", data or big_jpeg(), "image/jpeg")},
        data=form,
        headers=user.headers,
    )
    assert response.status_code == 202, response.text
    return note_id, response.json()["id"]


def _box(
    label: str, x: float = 0.1, y: float = 0.2, w: float = 0.3, h: float = 0.25, **extra: Any
) -> dict[str, Any]:
    return {"label_id": label, "x": x, "y": y, "w": w, "h": h, **extra}


async def _save(user: LoggedIn, note_id: str, att_id: str, **body: Any) -> Any:
    return await user.client.put(
        f"/notes/{note_id}/attachments/{att_id}/annotations", json=body, headers=user.headers
    )


async def test_preview_and_full_image_are_served_safely(
    h: Harness, factory: SessionFactory
) -> None:
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice, keep_location="true")
    base = f"/notes/{note_id}/attachments/{att_id}/image"

    preview = await alice.client.get(base)
    assert preview.status_code == 200
    headers = preview.headers
    assert headers["content-type"] == "image/jpeg"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "private, no-store"
    assert "default-src 'none'" in headers["content-security-policy"]
    with Image.open(io.BytesIO(preview.content)) as image:
        assert max(image.size) == 1600
        assert image.size == (1600, 1067)
        assert not image.getexif()  # the preview drops even the GPS the member chose to keep

    full = await alice.client.get(base, params={"size": "full"})
    with Image.open(io.BytesIO(full.content)) as image:
        assert image.size == (3000, 2000)
        assert GPS_IFD in image.getexif()  # the original keeps what the member chose

    async with rls_session(factory, UUID(alice.user_id)) as session:
        row = await session.get(NoteAttachment, UUID(att_id))
        assert row is not None
        assert (row.width, row.height) == (3000, 2000)
        preview_key = row.preview_object_key
    assert preview_key is not None
    # Both objects are ciphertext, never the JPEG itself.
    assert not h.storage.objects[preview_key].startswith(b"\xff\xd8")

    # Deleting the note removes the original and the preview.
    assert len(h.storage.objects) == 2
    deleted = await alice.client.delete(f"/notes/{note_id}", headers=alice.headers)
    assert deleted.status_code == 204
    assert h.storage.objects == {}


async def test_preview_is_created_on_first_view_for_older_photos(
    h: Harness, factory: SessionFactory
) -> None:
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice, big_jpeg(800, 600, gps=False))
    async with rls_session(factory, UUID(alice.user_id)) as session:
        row = await session.get(NoteAttachment, UUID(att_id))
        assert row is not None
        assert row.preview_object_key is not None
        await h.storage.delete(row.preview_object_key)
        row.preview_object_key, row.width, row.height = None, None, None  # as before 9.2

    response = await alice.client.get(f"/notes/{note_id}/attachments/{att_id}/image")
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (800, 600)  # never upscaled
    async with rls_session(factory, UUID(alice.user_id)) as session:
        row = await session.get(NoteAttachment, UUID(att_id))
        assert row is not None
        assert row.preview_object_key in h.storage.objects
        assert (row.width, row.height) == (800, 600)


async def test_photos_and_annotations_are_private(h: Harness, factory: SessionFactory) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    bob = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)
    saved = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        caption="Matka na ramce 3",
        regions=[_box(vocab.queen, description="znaczona na zielono")],
        version=0,
    )
    assert saved.status_code == 200, saved.text

    base = f"/notes/{note_id}/attachments/{att_id}"
    for path in (f"{base}/image", f"{base}/image?size=full", f"{base}/annotations"):
        response = await bob.client.get(path)
        assert response.status_code == 404
    overwrite = await _save(bob, note_id, att_id, domain_id=vocab.domain, regions=[], version=1)
    assert overwrite.status_code == 404

    # RLS at the database: Bob's session sees no rows; ciphertext only at rest.
    async with rls_session(factory, UUID(bob.user_id)) as session:
        assert await session.scalar(select(func.count()).select_from(AnnotatedImage)) == 0
        assert await session.scalar(select(func.count()).select_from(ImageAnnotation)) == 0
    async with factory() as session:  # no app.current_user_id at all
        assert await session.scalar(select(func.count()).select_from(AnnotatedImage)) == 0
    async with rls_session(factory, UUID(alice.user_id)) as session:
        image = await session.scalar(select(AnnotatedImage))
        assert image is not None
        assert image.caption_ciphertext is not None
        assert b"Matka" not in image.caption_ciphertext
        raw = await session.execute(text("SELECT description_ciphertext FROM image_annotation"))
        assert b"zielono" not in raw.scalar_one()


async def test_save_load_and_optimistic_locking(h: Harness, queue: RecordingQueue) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)
    path = f"/notes/{note_id}/attachments/{att_id}/annotations"

    empty = (await alice.client.get(path)).json()
    assert (empty["version"], empty["regions"], empty["domain_id"]) == (0, [], None)
    assert (empty["width"], empty["height"]) == (3000, 2000)

    first = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        caption=" Matka ",
        regions=[
            _box(vocab.queen, description="z kropką"),
            _box(vocab.cell, x=0.7, y=0.7, w=0.3, h=0.3),
        ],
        version=0,
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["version"] == 1
    assert body["caption"] == "Matka"
    assert [(r["label"]["name"], r["description"]) for r in body["regions"]] == [
        ("Matka pszczela", "z kropką"),
        ("Matecznik", None),
    ]
    assert (await alice.client.get(path)).json()["regions"] == body["regions"]

    stale = await _save(alice, note_id, att_id, domain_id=vocab.domain, regions=[], version=0)
    assert (stale.status_code, stale.json()["detail"]["error"]) == (409, "stale_version")
    second = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        caption="Matka",
        regions=[_box(vocab.queen)],
        version=1,
    )
    assert (second.json()["version"], len(second.json()["regions"])) == (2, 1)
    assert len(queue.saved) == 2  # each save is screened and indexed again


@pytest.mark.parametrize(
    ("region", "error"),
    [
        ({"x": 0.9, "w": 0.2}, "invalid_box"),  # beyond the right edge
        ({"w": 0.001}, "invalid_box"),  # under 0.5 % of a side
        ({"y": 0.5, "h": 0.6}, "invalid_box"),
    ],
)
async def test_boxes_are_validated(h: Harness, region: dict[str, float], error: str) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)
    response = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        regions=[_box(vocab.queen, **region)],
        version=0,  # pyright: ignore[reportArgumentType]
    )
    assert (response.status_code, response.json()["detail"]["error"]) == (422, error)


async def test_limits_and_schema_bounds(h: Harness) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)
    too_many = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        regions=[_box(vocab.queen)] * 51,
        version=0,
    )
    assert too_many.status_code == 422
    negative = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        regions=[_box(vocab.queen, x=-0.1)],
        version=0,
    )
    assert negative.status_code == 422
    long_caption = await _save(
        alice, note_id, att_id, domain_id=vocab.domain, caption="x" * 2001, regions=[], version=0
    )
    assert long_caption.status_code == 422


async def test_labels_must_belong_to_the_domain_and_be_usable(h: Harness) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    bob = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)

    foreign = await _save(
        alice, note_id, att_id, domain_id=vocab.domain, regions=[_box(vocab.foreign)], version=0
    )
    assert (foreign.status_code, foreign.json()["detail"]["error"]) == (404, "label_not_found")

    async def propose(user: LoggedIn, name: str) -> str:
        response = await user.client.post(
            "/atlas/labels/proposals",
            json={"domain_id": vocab.domain, "name": name},
            headers=user.headers,
        )
        return response.json()["id"]

    bobs = await propose(bob, "Kit")
    refused = await _save(
        alice, note_id, att_id, domain_id=vocab.domain, regions=[_box(bobs)], version=0
    )
    assert (refused.status_code, refused.json()["detail"]["error"]) == (422, "label_not_usable")
    mine = await propose(alice, "Miseczka")
    accepted = await _save(
        alice, note_id, att_id, domain_id=vocab.domain, regions=[_box(mine)], version=0
    )
    assert accepted.json()["regions"][0]["label"]["status"] == "PROPOSED"

    # A label deprecated after it was used stays on the photo when other things change.
    admin = await h.login(roles=["MEMBER", "ADMIN"], **MFA)
    await admin.client.post(f"/atlas/curation/labels/{mine}/approve", headers=admin.headers)
    await admin.client.post(f"/atlas/curation/labels/{mine}/deprecate", headers=admin.headers)
    resave = await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        caption="nowy opis",
        regions=[_box(mine)],
        version=1,
    )
    assert resave.status_code == 200, resave.text
    assert resave.json()["regions"][0]["label"]["status"] == "DEPRECATED"
    # …but it can't be put on another photo.
    note2, att2 = await _photo(alice)
    other = await _save(alice, note2, att2, domain_id=vocab.domain, regions=[_box(mine)], version=0)
    assert (other.status_code, other.json()["detail"]["error"]) == (422, "label_not_usable")

    # Archived domain: read-only.
    await admin.client.patch(
        f"/admin/atlas/domains/{vocab.domain}", json={"status": "ARCHIVED"}, headers=admin.headers
    )
    archived = await _save(alice, note_id, att_id, domain_id=vocab.domain, regions=[], version=2)
    assert (archived.status_code, archived.json()["detail"]["error"]) == (409, "domain_archived")
    still_readable = await alice.client.get(f"/notes/{note_id}/attachments/{att_id}/annotations")
    assert len(still_readable.json()["regions"]) == 1


async def test_annotations_become_a_notebook_derivative(
    h: Harness, factory: SessionFactory, queue: RecordingQueue
) -> None:
    vocab = await _vocabulary(h)
    alice = await h.login(roles=["MEMBER"])
    note_id, att_id = await _photo(alice)
    await _save(
        alice,
        note_id,
        att_id,
        domain_id=vocab.domain,
        caption="Ramka 3, ul 7",
        regions=[_box(vocab.queen, description="młoda matka")],
        version=0,
    )
    note = (await alice.client.get(f"/notes/{note_id}")).json()
    [derivative] = [d for d in note["derivatives"] if d["kind"] == "ANNOTATIONS"]
    assert derivative["status"] == "DONE"
    assert derivative["attachment_id"] == att_id
    assert derivative["content"] == (
        "Zdjęcie (Pszczelarstwo): Ramka 3, ul 7\nOznaczenia:\n- Matka pszczela: młoda matka"
    )
    assert [str(j.derivative_id) for j in queue.saved] == [derivative["id"]]

    # Emptying the photo drops the derivative (and its vectors, in the background).
    await _save(alice, note_id, att_id, domain_id=vocab.domain, regions=[], version=1)
    async with rls_session(factory, UUID(alice.user_id)) as session:
        left = await session.scalars(
            select(NoteDerivative).where(NoteDerivative.kind == DerivativeKind.ANNOTATIONS)
        )
        assert left.all() == []


async def test_non_images_have_no_photo_endpoints(h: Harness) -> None:
    alice = await h.login(roles=["MEMBER"])
    note = await alice.client.post("/notes", json={"title": "Dokument"}, headers=alice.headers)
    note_id = note.json()["id"]
    upload = await alice.client.post(
        f"/notes/{note_id}/attachments",
        files={"file": ("a.pdf", files.pdf(), "application/pdf")},
        headers=alice.headers,
    )
    att_id = upload.json()["id"]
    for path in ("image", "annotations"):
        response = await alice.client.get(f"/notes/{note_id}/attachments/{att_id}/{path}")
        assert response.status_code == 404
    # No preview was made for the PDF.
    assert len(h.storage.objects) == 1
