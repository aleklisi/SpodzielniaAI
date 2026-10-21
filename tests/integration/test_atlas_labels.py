"""Chunk 9.1: atlas domains and label vocabulary, with the real app and Postgres.

Members see ACTIVE labels and their own proposals and may only propose; EXPERT/ADMIN with MFA
curate labels; only ADMIN manages domains. Every change is audited."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.atlas.labels import LabelError, LabelService
from spoldzielnia_ai.atlas.seed import EXAMPLE_LABELS, seed_example
from spoldzielnia_ai.db.models import SecurityAuditLog
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from spoldzielnia_ai.core.settings import Settings
    from tests.fixtures.app import Harness, LoggedIn

pytestmark = pytest.mark.integration

MFA: dict[str, Any] = {"amr": ["pwd", "otp"]}


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def h(settings: Settings, factory: SessionFactory) -> AsyncIterator[Harness]:
    limited = settings.model_copy(
        update={"atlas": settings.atlas.model_copy(update={"label_proposals_per_day": 3})}
    )
    async with harness(limited, factory) as h:
        yield h


async def _admin(h: Harness) -> LoggedIn:
    return await h.login(roles=["MEMBER", "ADMIN"], **MFA)


async def _expert(h: Harness, **claims: Any) -> LoggedIn:
    return await h.login(roles=["MEMBER", "EXPERT"], **(claims or MFA))


async def _domain(admin: LoggedIn, name: str = "Pszczelarstwo") -> str:
    response = await admin.client.post(
        "/admin/atlas/domains", json={"name": name}, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _label(expert: LoggedIn, domain_id: str, name: str, **extra: Any) -> dict[str, Any]:
    response = await expert.client.post(
        "/atlas/curation/labels",
        json={"domain_id": domain_id, "name": name, **extra},
        headers=expert.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _events(factory: SessionFactory) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(SecurityAuditLog.event_type).order_by(SecurityAuditLog.id)
        )
        return [r for r in rows if r.startswith(("LABEL_", "DOMAIN_"))]


async def test_authorization_matrix(h: Harness) -> None:
    member = await h.login(roles=["MEMBER"])
    expert_without_mfa = await h.login(roles=["MEMBER", "EXPERT"])
    expert = await _expert(h)
    admin = await _admin(h)
    domain_id = await _domain(admin)

    # Only ADMIN (with MFA) manages domains.
    for user in (member, expert_without_mfa, expert):
        create = await user.client.post(
            "/admin/atlas/domains", json={"name": "Ogrodnictwo"}, headers=user.headers
        )
        assert create.status_code == 403
    # Only EXPERT/ADMIN with MFA curate labels.
    body = {"domain_id": domain_id, "name": "Matka pszczela"}
    for user in (member, expert_without_mfa):
        response = await user.client.post("/atlas/curation/labels", json=body, headers=user.headers)
        assert response.status_code == 403
        assert (await user.client.get("/atlas/curation/labels")).status_code == 403
    assert (await _label(expert, domain_id, "Matka pszczela"))["status"] == "ACTIVE"
    assert (await _label(admin, domain_id, "Truteń"))["status"] == "ACTIVE"
    # Every member reads domains and labels.
    assert [d["name"] for d in (await member.client.get("/atlas/domains")).json()] == [
        "Pszczelarstwo"
    ]
    labels = (await member.client.get("/atlas/labels", params={"domain_id": domain_id})).json()
    assert [label["name"] for label in labels] == ["Matka pszczela", "Truteń"]
    # CSRF still applies.
    assert (await admin.client.post("/admin/atlas/domains", json={"name": "X"})).status_code == 403


async def test_search_ignores_case_and_diacritics(h: Harness) -> None:
    admin = await _admin(h)
    domain_id = await _domain(admin)
    await _label(admin, domain_id, "Matka pszczela", synonyms=["Królowa", "matka"])
    await _label(admin, domain_id, "Pszczoła robotnica")
    member = await h.login(roles=["MEMBER"])

    async def names(q: str) -> list[str]:
        params = {"domain_id": domain_id, "q": q}
        return [x["name"] for x in (await member.client.get("/atlas/labels", params=params)).json()]

    assert await names("KROLOWA") == ["Matka pszczela"]
    assert await names("pszczoła") == ["Pszczoła robotnica"]
    assert await names("PSZCZEŁA") == ["Matka pszczela"]
    assert await names("pszczola rob") == ["Pszczoła robotnica"]
    assert await names("%") == []  # LIKE wildcards are literal


async def test_same_name_in_two_domains_but_not_twice_in_one(h: Harness) -> None:
    admin = await _admin(h)
    bees = await _domain(admin, "Pszczelarstwo")
    garden = await _domain(admin, "Ogrodnictwo")
    await _label(admin, bees, "Larwa")
    await _label(admin, garden, "Larwa")
    clash = await admin.client.post(
        "/atlas/curation/labels",
        json={"domain_id": bees, "name": "  LARWA "},
        headers=admin.headers,
    )
    assert (clash.status_code, clash.json()["detail"]["error"]) == (409, "label_exists")
    again = await admin.client.post(
        "/admin/atlas/domains", json={"name": "pszczelarstwo"}, headers=admin.headers
    )
    assert (again.status_code, again.json()["detail"]["error"]) == (409, "domain_exists")


async def test_proposals_are_private_until_approved_and_limited(h: Harness) -> None:
    admin = await _admin(h)
    domain_id = await _domain(admin)
    alice = await h.login(roles=["MEMBER"])
    bob = await h.login(roles=["MEMBER"])
    proposal = await alice.client.post(
        "/atlas/labels/proposals",
        json={"domain_id": domain_id, "name": "Miseczka mateczna", "description": "zaczątek"},
        headers=alice.headers,
    )
    assert proposal.status_code == 201, proposal.text
    label = proposal.json()
    assert (label["status"], label["is_own_proposal"]) == ("PROPOSED", True)

    def visible(user: LoggedIn) -> Any:
        return user.client.get("/atlas/labels", params={"domain_id": domain_id})

    assert [x["name"] for x in (await visible(alice)).json()] == ["Miseczka mateczna"]
    assert (await visible(bob)).json() == []

    expert = await _expert(h)
    queue = await expert.client.get(
        "/atlas/curation/labels", params={"status": "PROPOSED", "domain_id": domain_id}
    )
    assert [x["name"] for x in queue.json()] == ["Miseczka mateczna"]
    approved = await expert.client.post(
        f"/atlas/curation/labels/{label['id']}/approve", headers=expert.headers
    )
    assert approved.json()["status"] == "ACTIVE"
    assert [x["name"] for x in (await visible(bob)).json()] == ["Miseczka mateczna"]

    # Rejected proposals give their name back.
    for name in ("Pierzga", "Kit"):
        response = await bob.client.post(
            "/atlas/labels/proposals",
            json={"domain_id": domain_id, "name": name},
            headers=bob.headers,
        )
        assert response.status_code == 201
    kit = next(
        x for x in (await expert.client.get("/atlas/curation/labels", params={"q": "kit"})).json()
    )
    rejected = await expert.client.post(
        f"/atlas/curation/labels/{kit['id']}/reject",
        json={"reason": "duplikat"},
        headers=expert.headers,
    )
    assert rejected.json()["status"] == "REJECTED"
    assert (await _label(expert, domain_id, "Kit"))["status"] == "ACTIVE"
    # Daily limit (3 in this test): the 4th proposal of the day is refused.
    for name, expected in (("Propolis", 201), ("Wosk", 429)):
        response = await bob.client.post(
            "/atlas/labels/proposals",
            json={"domain_id": domain_id, "name": name},
            headers=bob.headers,
        )
        assert response.status_code == expected


async def test_merge_deprecate_and_archived_domain(h: Harness, factory: SessionFactory) -> None:
    admin = await _admin(h)
    domain_id = await _domain(admin)
    other_domain = await _domain(admin, "Ogrodnictwo")
    queen = await _label(admin, domain_id, "Matka pszczela")
    alias = await _label(admin, domain_id, "Królowa")
    foreign = await _label(admin, other_domain, "Mszyca")

    cross = await admin.client.post(
        f"/atlas/curation/labels/{foreign['id']}/merge",
        json={"into_id": queen["id"]},
        headers=admin.headers,
    )
    assert (cross.status_code, cross.json()["detail"]["error"]) == (422, "different_domain")
    merged = await admin.client.post(
        f"/atlas/curation/labels/{alias['id']}/merge",
        json={"into_id": queen["id"]},
        headers=admin.headers,
    )
    assert merged.json()["status"] == "DEPRECATED"
    assert merged.json()["merged_into_id"] == queen["id"]
    member = await h.login(roles=["MEMBER"])
    found = (
        await member.client.get("/atlas/labels", params={"domain_id": domain_id, "q": "krolowa"})
    ).json()
    assert [x["name"] for x in found] == ["Matka pszczela"]  # the old name is now a synonym

    async with factory() as session:
        labels = LabelService(session)
        resolved = await labels.resolve([UUID(alias["id"])])
        assert str(resolved[UUID(alias["id"])].id) == queen["id"]
        # A merged label is still usable on a new mark: it stands for its target.
        usable = await labels.usable(UUID(alias["id"]), UUID(member.user_id), UUID(domain_id))
        assert str(usable.id) == queen["id"]

    deprecated = await admin.client.post(
        f"/atlas/curation/labels/{foreign['id']}/deprecate", headers=admin.headers
    )
    assert deprecated.json()["status"] == "DEPRECATED"
    async with factory() as session:
        with pytest.raises(LabelError, match="label_not_usable"):
            await LabelService(session).usable(
                UUID(foreign["id"]), UUID(member.user_id), UUID(other_domain)
            )

    archived = await admin.client.patch(
        f"/admin/atlas/domains/{other_domain}", json={"status": "ARCHIVED"}, headers=admin.headers
    )
    assert archived.json()["status"] == "ARCHIVED"
    refused = await member.client.post(
        "/atlas/labels/proposals",
        json={"domain_id": other_domain, "name": "Przędziorek"},
        headers=member.headers,
    )
    assert (refused.status_code, refused.json()["detail"]["error"]) == (409, "domain_archived")

    assert await _events(factory) == [
        "DOMAIN_CREATED",
        "DOMAIN_CREATED",
        "LABEL_APPROVED",
        "LABEL_APPROVED",
        "LABEL_APPROVED",
        "LABEL_MERGED",
        "LABEL_DEPRECATED",
        "DOMAIN_CHANGED",
    ]


async def test_seed_example_is_idempotent(factory: SessionFactory) -> None:
    first = await seed_example(factory)
    second = await seed_example(factory)
    assert (first.domain_created, first.labels_created) == (True, len(EXAMPLE_LABELS))
    assert (second.domain_id, second.domain_created, second.labels_created) == (
        first.domain_id,
        False,
        0,
    )
