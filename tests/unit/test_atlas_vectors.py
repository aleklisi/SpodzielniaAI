"""Chunk 9.8: atlas index points carry provenance and no text; scopes never select everything."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from PIL import Image

from spoldzielnia_ai.atlas.embedding import MIN_CROP_PX, Box, crop
from spoldzielnia_ai.atlas.vectors import AtlasScope, atlas_payload, point_id, relabel
from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus
from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion


def test_crop_has_a_margin_and_a_minimum_size() -> None:
    image = Image.new("RGB", (1000, 500))
    assert crop(image, Box(0.4, 0.4, 0.2, 0.2)).size == (240, 120)  # 10 % on each side
    tiny = crop(image, Box(0.5, 0.5, 0.01, 0.01))
    assert min(tiny.size) >= MIN_CROP_PX
    corner = crop(image, Box(0.0, 0.0, 0.1, 0.04))  # shifted inward at the border
    assert corner.size == (120, MIN_CROP_PX)


def test_scope_round_trip_and_guard() -> None:
    scope = AtlasScope(image_ids=(uuid.uuid4(),))
    assert AtlasScope.from_json(scope.to_json()) == scope
    assert scope.to_json()["target"] == "atlas"
    with pytest.raises(ValueError, match="needs image_ids or label_id"):
        AtlasScope()
    with pytest.raises(ValueError, match="needs image_ids or label_id"):
        AtlasScope(image_ids=())
    old, new = uuid.uuid4(), uuid.uuid4()
    assert relabel(old, new).payload == {"label_id": str(new)}


def test_payload_has_provenance_and_no_text() -> None:
    image = AtlasImage(
        id=uuid.uuid4(),
        author_id=uuid.uuid4(),
        domain_id=uuid.uuid4(),
        status=AtlasImageStatus.APPROVED,
        consent_status=ConsentStatus.ACTIVE,
        caption="Matka z ula 7",
        verified_at=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    region = AtlasRegion(id=uuid.uuid4(), description="znaczona na zielono")
    label = uuid.uuid4()
    payload = atlas_payload(image, region, label)
    assert payload["author_id"] == str(image.author_id)
    assert (payload["consent_status"], payload["is_active"], payload["kind"]) == (
        "ACTIVE",
        True,
        "region",
    )
    assert "Matka" not in str(payload)
    assert "zielono" not in str(payload)
    image.consent_status = ConsentStatus.SUSPENDED
    assert atlas_payload(image, None, None)["is_active"] is False
    assert point_id(image.id, None) == point_id(image.id, None) != point_id(image.id, region.id)


def test_benchmark_leave_one_out() -> None:
    from spoldzielnia_ai.atlas.benchmark import evaluate

    queen, cell = "q", "c"
    rows = [
        ("p1", queen, [1.0, 0.0]),
        ("p2", queen, [0.96, 0.28]),
        ("p3", cell, [0.0, 1.0]),
        ("p4", cell, [0.28, 0.96]),
    ]
    report = evaluate(rows)
    assert (report.regions, report.photos, report.labels) == (4, 4, 2)
    assert report.top1 == 1.0
    assert report.suggested_min_score is not None
    assert evaluate(rows[:1]).top1 is None  # one photo: nothing to compare with
