"""Chunk 9.7: dataset export (A9). Off by default, ADMIN + MFA + fresh login, only approved,
consented, export-allowed photos, COCO/YOLO with a manifest, single-use download, audit."""

from __future__ import annotations

import datetime as dt
import io
import json
import time
import zipfile
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.atlas.export import ExportBuilder
from spoldzielnia_ai.db.models import AtlasExport, SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import harness
from tests.fixtures.atlas import (
    MFA,
    CleanScanner,
    RecordingQueue,
    World,
    approve_all,
    make_world,
    sent_photo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration


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


@pytest.fixture
async def world(h: Harness, factory: SessionFactory, queue: RecordingQueue) -> World:
    return await make_world(h, factory, queue)


def enable(h: Harness, **extra: Any) -> None:
    settings: Settings = h.app.state.settings
    h.app.state.settings = settings.model_copy(
        update={"atlas": settings.atlas.model_copy(update={"export_enabled": True, **extra})}
    )


async def admin(h: Harness, *, fresh: bool = True) -> LoggedIn:
    auth_time = int(time.time()) - (0 if fresh else 3600)
    return await h.login(roles=["MEMBER", "ADMIN"], auth_time=auth_time, **MFA)


async def build(world: World) -> None:
    job = world.queue.exports.pop()
    state = world.h.app.state
    await ExportBuilder(world.factory, state.cipher, world.h.storage).handle(job.export_id)


async def create(user: LoggedIn, world: World, fmt: str = "COCO", **extra: Any) -> Any:
    return await user.client.post(
        "/admin/atlas/exports",
        json={"format": fmt, "domain_id": world.domain, **extra},
        headers=user.headers,
    )


async def photos(world: World) -> tuple[LoggedIn, list[str]]:
    """Three approved photos: exportable, not allowed, suspended."""
    alice = await world.h.login(roles=["MEMBER"], auth_time=int(time.time()))
    allowed = await sent_photo(world, alice, "orange", allow_export=True)
    refused = await sent_photo(world, alice, "green", allow_export=False)
    suspended = await sent_photo(world, alice, "blue", allow_export=True)
    for image_id in (allowed, refused, suspended):
        await approve_all(world.h, image_id)
    response = await alice.client.post(
        f"/me/contributions/atlas/{suspended}/suspend", headers=alice.headers
    )
    assert response.status_code == 200
    return alice, [allowed, refused, suspended]


async def test_disabled_by_default_and_admin_only(world: World) -> None:
    h = world.h
    boss = await admin(h)
    disabled = await create(boss, world)
    assert (disabled.status_code, disabled.json()["detail"]["error"]) == (403, "export_disabled")
    listing = (await boss.client.get("/admin/atlas/exports")).json()
    assert listing == {"enabled": False, "exports": []}

    enable(h)
    for user in (
        await h.login(roles=["MEMBER"]),
        await h.login(roles=["MEMBER", "EXPERT"], **MFA),
        await h.login(roles=["MEMBER", "ADMIN"]),  # no MFA
    ):
        assert (await create(user, world)).status_code == 403
    stale = await create(await admin(h, fresh=False), world)
    assert (stale.status_code, stale.json()["detail"]["error"]) == (
        403,
        "reauthentication_required",
    )
    assert "step_up" in stale.json()["detail"]


async def test_coco_export_contents_and_single_use_download(
    world: World, factory: SessionFactory
) -> None:
    h = world.h
    alice, (allowed, _refused, _suspended) = await photos(world)
    enable(h)
    boss = await admin(h)
    created = await create(boss, world, "COCO")
    assert created.status_code == 202, created.text
    token = created.json()["download_token"]
    export_id = created.json()["export"]["id"]
    assert created.json()["export"]["status"] == "PENDING"
    await build(world)

    listing = (await boss.client.get("/admin/atlas/exports")).json()["exports"]
    [entry] = listing
    assert (entry["status"], entry["image_count"], entry["region_count"]) == ("READY", 1, 2)

    wrong = await boss.client.get(
        f"/admin/atlas/exports/{export_id}/download", params={"token": "x" * 40}
    )
    assert wrong.status_code == 404
    download = await boss.client.get(
        f"/admin/atlas/exports/{export_id}/download", params={"token": token}
    )
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    again = await boss.client.get(
        f"/admin/atlas/exports/{export_id}/download", params={"token": token}
    )
    assert (again.status_code, again.json()["detail"]["error"]) == (409, "already_downloaded")
    async with factory() as session:
        row = await session.get(AtlasExport, UUID(export_id))
        assert row is not None
        assert row.object_key is None  # the archive is deleted after the download

    archive = zipfile.ZipFile(io.BytesIO(download.content))
    names = set(archive.namelist())
    coco = json.loads(archive.read("annotations.json"))
    manifest = json.loads(archive.read("manifest.json"))
    [image] = coco["images"]
    assert names == {"annotations.json", "manifest.json", image["file_name"]}
    assert [c["name"] for c in coco["categories"]] == ["Matecznik", "Matka pszczela"]
    with Image.open(io.BytesIO(archive.read(image["file_name"]))) as jpeg:
        assert jpeg.size == (image["width"], image["height"])
        assert not jpeg.getexif()
    for annotation in coco["annotations"]:
        x, y, w, hgt = annotation["bbox"]
        assert annotation["image_id"] == image["id"]
        assert min(x, y) >= 0
        assert x + w <= image["width"] + 0.01
        assert y + hgt <= image["height"] + 0.01
    queen = next(a for a in coco["annotations"] if a["category_id"] == 2)
    assert queen["bbox"] == [120.0, 160.0, 360.0, 200.0]  # 0.1/0.2/0.3/0.25 of 1200 x 800
    [photo] = manifest["photos"]
    assert photo["labels"] == ["Matecznik", "Matka pszczela"]
    assert manifest["licence"].startswith("Do użytku wewnętrznego")
    # No ids that lead back to an account or to the atlas photo.
    blob = b"".join(archive.read(n) for n in names if not n.endswith(".jpg"))
    assert alice.user_id.encode() not in blob
    assert allowed.encode() not in blob

    async with factory() as session:
        rows = (
            await session.scalars(
                select(SecurityAuditLog).where(SecurityAuditLog.event_type.like("ATLAS_EXPORT%"))
            )
        ).all()
    events = {r.event_type: r.payload for r in rows}
    assert set(events) == {"ATLAS_EXPORT_REQUESTED", "ATLAS_EXPORTED", "ATLAS_EXPORT_DOWNLOADED"}
    assert events["ATLAS_EXPORTED"]["manifest_sha256"] == entry["manifest_sha256"]


async def test_yolo_labels_and_label_filter(world: World) -> None:
    h = world.h
    await photos(world)
    enable(h)
    boss = await admin(h)
    created = await create(boss, world, "YOLO", label_ids=[world.queen])
    await build(world)
    token, export_id = created.json()["download_token"], created.json()["export"]["id"]
    download = await boss.client.get(
        f"/admin/atlas/exports/{export_id}/download", params={"token": token}
    )
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    assert archive.read("classes.txt").decode() == "Matka pszczela\n"
    [labels] = [n for n in archive.namelist() if n.startswith("labels/")]
    assert archive.read(labels).decode() == "0 0.250000 0.325000 0.300000 0.250000\n"


async def test_revokes_after_export_show_up_and_links_expire(
    world: World, factory: SessionFactory
) -> None:
    h = world.h
    alice, (allowed, *_) = await photos(world)
    enable(h)
    boss = await admin(h)
    created = await create(boss, world)
    await build(world)
    export_id = created.json()["export"]["id"]
    revoked = await alice.client.post(
        f"/me/contributions/atlas/{allowed}/revoke",
        json={"confirm": "REVOKE"},
        headers=alice.headers,
    )
    assert revoked.status_code == 200, revoked.text
    [entry] = (await boss.client.get("/admin/atlas/exports")).json()["exports"]
    assert entry["revoked_since"] == 1

    async with factory() as session, session.begin():
        row = await session.get(AtlasExport, UUID(export_id))
        assert row is not None
        key = row.object_key
        row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    [entry] = (await boss.client.get("/admin/atlas/exports")).json()["exports"]
    assert entry["status"] == "EXPIRED"
    assert key not in h.storage.objects


async def test_daily_limit(world: World) -> None:
    enable(world.h, exports_per_day=1)
    boss = await admin(world.h)
    assert (await create(boss, world)).status_code == 202
    assert (await create(boss, world)).status_code == 429
