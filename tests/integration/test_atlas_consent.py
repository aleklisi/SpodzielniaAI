"""Chunk 9.6: the author's consent over atlas photos (A6) and their two choices (A9, A12)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.enums import ConsentStatus
from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion, SecurityAuditLog
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

    from httpx import Response

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


async def action(user: LoggedIn, image_id: str, name: str, **body: object) -> Response:
    return await user.client.post(
        f"/me/contributions/atlas/{image_id}/{name}", json=body or None, headers=user.headers
    )


async def approved(world: World, user: LoggedIn, color: str = "orange", **choices: bool) -> str:
    image_id = await sent_photo(world, user, color, **choices)
    await approve_all(world.h, image_id)
    return image_id


async def test_suspend_and_resume(world: World, factory: SessionFactory) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"], auth_time=int(time.time()))
    image_id = await approved(world, alice)
    # An expert adds a mark: it follows the photo's consent.
    expert = await h.login(roles=["MEMBER", "EXPERT"], **MFA)
    bob = await h.login(roles=["MEMBER"])
    assert (await bob.client.get(f"/atlas/photos/{image_id}")).status_code == 200

    assert (await action(bob, image_id, "suspend")).status_code == 404  # not Bob's photo
    suspended = await action(alice, image_id, "suspend")
    assert suspended.json()["consent_status"] == "SUSPENDED"
    assert (await bob.client.get(f"/atlas/photos/{image_id}")).status_code == 404
    assert (await bob.client.get("/atlas/photos")).json()["total"] == 0
    assert (await alice.client.get(f"/atlas/photos/{image_id}")).status_code == 200  # still hers
    async with factory() as session:
        statuses = set((await session.scalars(select(AtlasRegion.consent_status))).all())
    assert statuses == {ConsentStatus.SUSPENDED}
    again = await action(alice, image_id, "suspend")
    assert (again.status_code, again.json()["detail"]["error"]) == (409, "consent_unchanged")

    resumed = await action(alice, image_id, "resume")
    assert resumed.json()["consent_status"] == "ACTIVE"
    assert (await bob.client.get(f"/atlas/photos/{image_id}")).status_code == 200
    del expert


async def test_revoke_is_final_and_removes_everything(
    world: World, factory: SessionFactory
) -> None:
    h = world.h
    stale = await h.login(roles=["MEMBER"], auth_time=int(time.time()) - 3600)
    image_id = await approved(world, stale)
    no_confirm = await action(stale, image_id, "revoke")
    assert no_confirm.status_code == 422
    step_up = await action(stale, image_id, "revoke", confirm="REVOKE")
    assert (step_up.status_code, step_up.json()["detail"]["error"]) == (
        403,
        "reauthentication_required",
    )

    # A fresh login of the same account.
    fresh = await h.login(
        subject=await _subject(factory, stale.user_id), roles=["MEMBER"], auth_time=int(time.time())
    )
    async with factory() as session:
        image = await session.get(AtlasImage, UUID(image_id))
        assert image is not None
        keys = {image.object_key, image.preview_object_key, image.thumb_object_key}
    assert keys <= set(h.storage.objects)
    revoked = await action(fresh, image_id, "revoke", confirm="REVOKE")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["consent_status"] == "REVOKED"
    assert not keys & set(h.storage.objects)  # objects deleted after commit
    async with factory() as session:
        image = await session.get(AtlasImage, UUID(image_id))
        regions = (await session.scalars(select(AtlasRegion))).all()
    assert image is not None
    assert (image.caption, image.object_key, image.allow_export) == (None, None, False)
    assert {(r.consent_status, r.description) for r in regions} == {(ConsentStatus.REVOKED, None)}
    for path in (f"/atlas/photos/{image_id}", f"/atlas/images/{image_id}/image"):
        assert (await fresh.client.get(path)).status_code == 404  # not even for the author
    final = await action(fresh, image_id, "resume")
    assert (final.status_code, final.json()["detail"]["error"]) == (409, "consent_unchanged")
    prefs = await fresh.client.patch(
        f"/me/contributions/atlas/{image_id}", json={"show_author": True}, headers=fresh.headers
    )
    assert (prefs.status_code, prefs.json()["detail"]["error"]) == (409, "photo_revoked")


async def test_name_and_export_choices(world: World, factory: SessionFactory) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"], name="Alicja Pszczelarska")
    image_id = await approved(world, alice, show_author=False, allow_export=False)
    bob = await h.login(roles=["MEMBER"])

    async def author() -> str | None:
        return (await bob.client.get(f"/atlas/photos/{image_id}")).json()["author_name"]

    assert await author() is None
    named = await alice.client.patch(
        f"/me/contributions/atlas/{image_id}",
        json={"show_author": True, "allow_export": True},
        headers=alice.headers,
    )
    assert (named.json()["show_author"], named.json()["allow_export"]) == (True, True)
    assert await author() == "Alicja Pszczelarska"
    await alice.client.patch(
        f"/me/contributions/atlas/{image_id}", json={"show_author": False}, headers=alice.headers
    )
    assert await author() is None

    mine = (await alice.client.get("/me/contributions/atlas")).json()
    assert [(m["id"], m["labels"], m["allow_export"]) for m in mine] == [
        (image_id, ["Matka pszczela", "Matecznik"], True)
    ]
    assert (await bob.client.get("/me/contributions/atlas")).json() == []
    async with factory() as session:
        events = (
            await session.scalars(
                select(SecurityAuditLog.event_type).where(
                    SecurityAuditLog.event_type.like("ATLAS_%_CHANGED")
                )
            )
        ).all()
    assert sorted(events) == [
        "ATLAS_ATTRIBUTION_CHANGED",
        "ATLAS_ATTRIBUTION_CHANGED",
        "ATLAS_EXPORT_CONSENT_CHANGED",
        "ATLAS_STATUS_CHANGED",
    ]


async def _subject(factory: SessionFactory, user_id: str) -> str:
    from spoldzielnia_ai.db.models import User

    async with factory() as session:
        user = await session.get(User, UUID(user_id))
        assert user is not None
        return user.oidc_subject
