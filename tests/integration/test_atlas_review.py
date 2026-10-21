"""Chunk 9.5: expert review of atlas photos and the shared atlas.

Quorum and optimistic locking, corrections with provenance, PROPOSED labels block approval,
members see only APPROVED + ACTIVE photos (A5), anonymous photos expose no author (A12), and
viewing photos spends the coverage budget so a scraper is stopped (A7).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion, SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import harness
from tests.fixtures.atlas import (
    MFA,
    CleanScanner,
    RecordingQueue,
    World,
    decide,
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
def tuned(settings: Settings) -> Settings:
    """Two experts must approve."""
    return settings.model_copy(
        update={"governance": settings.governance.model_copy(update={"review_quorum": 2})}
    )


def tune(h: Harness, **sections: dict[str, Any]) -> None:
    """Change settings for the rest of a test (the app reads them per request)."""
    settings: Settings = h.app.state.settings
    h.app.state.settings = settings.model_copy(
        update={
            name: getattr(settings, name).model_copy(update=values)
            for name, values in sections.items()
        }
    )


@pytest.fixture
async def h(
    tuned: Settings, factory: SessionFactory, queue: RecordingQueue
) -> AsyncIterator[Harness]:
    async with harness(tuned, factory) as h:
        h.app.state.scanner = CleanScanner()
        h.app.state.processing_queue = queue
        yield h


@pytest.fixture
async def world(h: Harness, factory: SessionFactory, queue: RecordingQueue) -> World:
    return await make_world(h, factory, queue)


async def experts(h: Harness) -> tuple[LoggedIn, LoggedIn]:
    return (
        await h.login(roles=["MEMBER", "EXPERT"], **MFA),
        await h.login(roles=["MEMBER", "EXPERT"], **MFA),
    )


async def approve(h: Harness, image_id: str) -> None:
    e1, e2 = await experts(h)
    version = (await e1.client.get(f"/atlas/review/{image_id}")).json()["version"]
    for expert in (e1, e2):
        response = await decide(expert, image_id, "APPROVE", version)
        assert response.status_code == 200, response.text


async def test_authorization_and_author_privacy_in_review(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"], name="Alicja Pszczelarska")
    image_id = await sent_photo(world, alice)
    member = await h.login(roles=["MEMBER"])
    expert_no_mfa = await h.login(roles=["MEMBER", "EXPERT"])
    for user in (member, expert_no_mfa):
        assert (await user.client.get("/atlas/review/queue")).status_code == 403
        refused = await decide(user, image_id, "APPROVE", 1)
        assert refused.status_code == 403
    expert, _ = await experts(h)
    queue = (await expert.client.get("/atlas/review/queue")).json()
    assert [(q["id"], q["regions"], q["approvals"], q["quorum"], q["is_own"]) for q in queue] == [
        (image_id, 2, 0, 2, False)
    ]
    detail = await expert.client.get(f"/atlas/review/{image_id}")
    assert detail.status_code == 200
    text = detail.text + json.dumps(queue)
    assert alice.user_id not in text  # reviewers never see who sent it
    assert "Alicja" not in text


async def test_quorum_and_locking(world: World, factory: SessionFactory) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    image_id = await sent_photo(world, alice)
    e1, e2 = await experts(h)

    stale = await decide(e1, image_id, "APPROVE", 7)
    assert (stale.status_code, stale.json()["detail"]["error"]) == (409, "stale_version")
    first = await decide(e1, image_id, "APPROVE", 1)
    assert (first.json()["status"], first.json()["approvals"]) == ("IN_REVIEW", 1)
    twice = await decide(e1, image_id, "APPROVE", 1)
    assert (twice.status_code, twice.json()["detail"]["error"]) == (409, "already_approved")
    member = await h.login(roles=["MEMBER"])
    assert (await member.client.get(f"/atlas/photos/{image_id}")).status_code == 404

    second = await decide(e2, image_id, "APPROVE", 1)
    assert (second.json()["previous"], second.json()["status"]) == ("IN_REVIEW", "APPROVED")
    assert (await member.client.get(f"/atlas/photos/{image_id}")).status_code == 200
    async with factory() as session:
        image = await session.get(AtlasImage, UUID(image_id))
        assert image is not None
        assert image.verified_at is not None
        events = (
            await session.scalars(
                select(SecurityAuditLog.event_type).where(
                    SecurityAuditLog.event_type.in_(["ATLAS_REVIEWED", "ATLAS_STATUS_CHANGED"])
                )
            )
        ).all()
    assert sorted(events) == ["ATLAS_REVIEWED", "ATLAS_REVIEWED", "ATLAS_STATUS_CHANGED"]
    # Approved: out of review.
    late = await decide(
        await h.login(roles=["MEMBER", "EXPERT"], **MFA), image_id, "REJECT", 1, "x"
    )
    assert (late.status_code, late.json()["detail"]["error"]) == (409, "not_in_review")


async def test_nobody_reviews_their_own_photo(world: World) -> None:
    author_expert = await world.h.login(roles=["MEMBER", "EXPERT"], **MFA)
    image_id = await sent_photo(world, author_expert)
    queue = (await author_expert.client.get("/atlas/review/queue")).json()
    assert queue[0]["is_own"] is True
    own = await decide(author_expert, image_id, "APPROVE", 1)
    assert (own.status_code, own.json()["detail"]["error"]) == (403, "own_photo")


async def test_corrections_keep_provenance_and_reset_approvals(
    world: World, factory: SessionFactory
) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    image_id = await sent_photo(world, alice)
    e1, e2 = await experts(h)
    await decide(e1, image_id, "APPROVE", 1)
    photo = (await e2.client.get(f"/atlas/review/{image_id}")).json()
    queen, _ = photo["regions"]
    body = {
        "version": 1,
        "regions": [
            {
                **{k: queen[k] for k in ("id", "x", "y", "w", "h", "description")},
                "label_id": queen["label"]["id"],
                "w": 0.35,
            },
            # the queen cell removed, a new mark added by the expert
            {
                "label_id": world.cell,
                "x": 0.5,
                "y": 0.5,
                "w": 0.1,
                "h": 0.1,
                "description": "drugi matecznik",
            },
        ],
    }
    corrected = await e2.client.put(
        f"/atlas/review/{image_id}/regions", json=body, headers=e2.headers
    )
    assert corrected.status_code == 200, corrected.text
    out = corrected.json()
    assert (out["version"], out["approvals"]) == (2, 0)  # e1's approval was for version 1
    assert [(r["corrected"], r["by_reviewer"]) for r in out["regions"]] == [
        (True, False),
        (False, True),
    ]
    async with factory() as session:
        rows = (await session.scalars(select(AtlasRegion).order_by(AtlasRegion.position))).all()
    assert [(str(r.author_id), str(r.corrected_by) if r.corrected_by else None) for r in rows] == [
        (alice.user_id, e2.user_id),
        (e2.user_id, None),
    ]
    stale = await e1.client.put(f"/atlas/review/{image_id}/regions", json=body, headers=e1.headers)
    assert (stale.status_code, stale.json()["detail"]["error"]) == (409, "stale_version")


async def test_proposed_labels_block_approval(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    proposal = await alice.client.post(
        "/atlas/labels/proposals",
        json={"domain_id": world.domain, "name": "Miseczka mateczna"},
        headers=alice.headers,
    )
    world.cell = proposal.json()["id"]  # the photo's second mark uses the proposal
    image_id = await sent_photo(world, alice)
    e1, _ = await experts(h)
    assert (await e1.client.get("/atlas/review/queue")).json()[0]["proposed_labels"] == 1
    blocked = await decide(e1, image_id, "APPROVE", 1)
    assert (blocked.status_code, blocked.json()["detail"]["error"]) == (
        409,
        "proposed_labels_pending",
    )
    await e1.client.post(f"/atlas/curation/labels/{world.cell}/approve", headers=e1.headers)
    assert (await decide(e1, image_id, "APPROVE", 1)).status_code == 200


async def test_request_changes_and_reject(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    first = await sent_photo(world, alice, "orange")
    second = await sent_photo(world, alice, "green")
    e1, _ = await experts(h)
    no_comment = await decide(e1, first, "REQUEST_CHANGES", 1)
    assert (no_comment.status_code, no_comment.json()["detail"]["error"]) == (
        422,
        "comment_required",
    )
    back = await decide(e1, first, "REQUEST_CHANGES", 1, "Zaznacz całą matkę")
    assert back.json()["status"] == "REVISION_REQUIRED"
    rejected = await decide(e1, second, "REJECT", 1, "To nie jest matka")
    assert rejected.json()["status"] == "REJECTED"
    # The author sees the alert on their submission; members see neither photo.
    subs = await alice.client.get(
        "/atlas/submissions", params={"attachment_id": await _attachment_of(world, first)}
    )
    assert subs.json()[0]["atlas"]["review_alert"] == "Zaznacz całą matkę"
    member = await h.login(roles=["MEMBER"])
    for image_id in (first, second):
        assert (await member.client.get(f"/atlas/photos/{image_id}")).status_code == 404
    assert (await member.client.get("/atlas/photos")).json()["total"] == 0


async def _attachment_of(world: World, image_id: str) -> str:
    """The private attachment an atlas photo came from (the owner's RLS session)."""
    from spoldzielnia_ai.db.models import AnnotatedImage, AtlasSubmission
    from spoldzielnia_ai.db.rls import rls_session

    async with rls_session(world.factory, await _author(world, image_id)) as session:
        found = await session.scalar(
            select(AnnotatedImage.attachment_id)
            .join(AtlasSubmission, AtlasSubmission.annotated_image_id == AnnotatedImage.id)
            .where(AtlasSubmission.atlas_image_id == UUID(image_id))
        )
    return str(found)


async def _author(world: World, image_id: str) -> UUID:
    async with world.factory() as session:
        image = await session.get(AtlasImage, UUID(image_id))
        assert image is not None
        return image.author_id


async def test_gallery_filters_and_author_choice(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"], name="Alicja Pszczelarska")
    named = await sent_photo(world, alice, "orange", show_author=True)
    anonymous = await sent_photo(world, alice, "green", show_author=False)
    for image_id in (named, anonymous):
        await approve(h, image_id)

    bob = await h.login(roles=["MEMBER"])
    page = (await bob.client.get("/atlas/photos", params={"domain_id": world.domain})).json()
    assert page["total"] == 2
    authors = {item["id"]: item["author_name"] for item in page["items"]}
    assert authors == {named: "Alicja Pszczelarska", anonymous: None}
    by_label = await bob.client.get("/atlas/photos", params={"label_id": world.cell})
    assert by_label.json()["total"] == 2
    by_text = await bob.client.get("/atlas/photos", params={"q": "MATKA PSZCZELA"})
    assert by_text.json()["total"] == 2
    assert (await bob.client.get("/atlas/photos", params={"q": "mszyca"})).json()["total"] == 0
    capped = await bob.client.get("/atlas/photos", params={"limit": 200})
    assert capped.json()["limit"] == 48

    detail = await bob.client.get(f"/atlas/photos/{anonymous}")
    body: dict[str, Any] = detail.json()
    assert body["author_name"] is None
    assert alice.user_id not in detail.text  # A12: no author id or name for an anonymous photo
    assert "Alicja" not in detail.text
    assert [r["label"]["name"] for r in body["regions"]] == ["Matka pszczela", "Matecznik"]
    assert len(body["reviewers"]) == 2
    own = (await alice.client.get(f"/atlas/photos/{anonymous}")).json()
    assert own["is_own"] is True


async def test_viewing_the_atlas_spends_coverage(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    ids = [await sent_photo(world, alice, c) for c in ("orange", "green", "blue")]
    for image_id in ids:
        await approve(h, image_id)
    # A tiny atlas can still trip coverage: floor 1, 24h limit 50 % (7d/30d out of reach).
    tune(
        h,
        shield={
            "min_blocks_before_lock": 1,
            "coverage_24h_max": 0.5,
            "coverage_7d_max": 0.9,
            "coverage_30d_max": 0.95,
        },
    )

    # The author browses her own photos freely.
    for image_id in ids:
        assert (await alice.client.get(f"/atlas/photos/{image_id}")).status_code == 200

    scraper = await h.login(roles=["MEMBER"])
    # Thumbnails in lists cost nothing.
    for image_id in ids:
        thumb = await scraper.client.get(
            f"/atlas/images/{image_id}/image", params={"size": "thumb"}
        )
        assert thumb.status_code == 200
    assert (await scraper.client.get(f"/atlas/photos/{ids[0]}")).status_code == 200  # 1/3 ≤ 50 %
    # Re-viewing the same photo doesn't count twice.
    assert (await scraper.client.get(f"/atlas/images/{ids[0]}/image")).status_code == 200
    throttled = await scraper.client.get(f"/atlas/photos/{ids[1]}")  # 2/3 > 50 %: refused
    assert throttled.status_code == 429


async def test_full_images_are_rate_limited(world: World) -> None:
    h = world.h
    alice = await h.login(roles=["MEMBER"])
    image_id = await sent_photo(world, alice)
    await approve(h, image_id)
    tune(h, atlas={"image_views_per_hour": 3})
    viewer = await h.login(roles=["MEMBER"])
    codes = [
        (
            await viewer.client.get(f"/atlas/images/{image_id}/image", params={"size": "full"})
        ).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
