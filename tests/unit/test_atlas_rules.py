"""Chunk 9.4: who sees an atlas photo (A5), privacy flags, near-duplicate hashing."""

from __future__ import annotations

import io
import uuid
from typing import TYPE_CHECKING

import pytest
from PIL import Image, ImageDraw

from spoldzielnia_ai.atlas.access import can_view
from spoldzielnia_ai.atlas.privacy import flagged, needs_people_confirmation
from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus, Role
from spoldzielnia_ai.db.models import AtlasImage
from spoldzielnia_ai.files.preview import dhash, hamming

if TYPE_CHECKING:
    from pathlib import Path

AUTHOR = uuid.uuid4()
OTHER = uuid.uuid4()
MEMBER = (Role.MEMBER,)
EXPERT = (Role.MEMBER, Role.EXPERT)


def image(status: AtlasImageStatus, consent: ConsentStatus = ConsentStatus.ACTIVE) -> AtlasImage:
    return AtlasImage(author_id=AUTHOR, status=status, consent_status=consent)


@pytest.mark.parametrize(
    ("status", "consent", "author", "member", "expert", "expert_without_mfa"),
    [
        (AtlasImageStatus.DRAFT, ConsentStatus.ACTIVE, True, False, False, False),
        (AtlasImageStatus.IN_REVIEW, ConsentStatus.ACTIVE, True, False, True, False),
        (AtlasImageStatus.REVISION_REQUIRED, ConsentStatus.ACTIVE, True, False, True, False),
        (AtlasImageStatus.APPROVED, ConsentStatus.ACTIVE, True, True, True, True),
        (AtlasImageStatus.APPROVED, ConsentStatus.SUSPENDED, True, False, False, False),
        (AtlasImageStatus.REJECTED, ConsentStatus.ACTIVE, True, False, False, False),
        (AtlasImageStatus.APPROVED, ConsentStatus.REVOKED, False, False, False, False),
    ],
)
def test_visibility_matrix(
    status: AtlasImageStatus,
    consent: ConsentStatus,
    author: bool,
    member: bool,
    expert: bool,
    expert_without_mfa: bool,
) -> None:
    row = image(status, consent)
    assert can_view(row, AUTHOR, MEMBER, mfa=False) is author
    assert can_view(row, OTHER, MEMBER, mfa=True) is member
    assert can_view(row, OTHER, EXPERT, mfa=True) is expert
    assert can_view(row, OTHER, EXPERT, mfa=False) is expert_without_mfa


def test_privacy_flags() -> None:
    flags = {"check": "ok", "people": False, "faces": True, "documents": True}
    assert flagged(flags) == ["faces", "documents"]
    assert needs_people_confirmation(flags)
    assert not needs_people_confirmation({"check": "ok", "documents": True})
    assert not needs_people_confirmation({"check": "unavailable"})


def _photo(tmp_path: Path, name: str, seed: int, *, quality: int = 90, scale: float = 1.0) -> Path:
    import random

    rng = random.Random(seed)
    img = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(img)
    for _ in range(30):
        x, y = rng.randrange(800), rng.randrange(600)
        draw.rectangle(
            (x, y, x + rng.randrange(40, 300), y + rng.randrange(40, 200)),
            fill=(rng.randrange(256),) * 3,
        )
    if scale != 1.0:
        img = img.resize((int(800 * scale), int(600 * scale)))
    path = tmp_path / name
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=quality)
    path.write_bytes(buffer.getvalue())
    return path


def test_dhash_is_stable_under_reencoding_and_differs_between_photos(tmp_path: Path) -> None:
    original = dhash(_photo(tmp_path, "a.jpg", 1), 10**8)
    resaved = dhash(_photo(tmp_path, "b.jpg", 1, quality=60, scale=0.5), 10**8)
    other = dhash(_photo(tmp_path, "c.jpg", 2), 10**8)
    assert len(original) == 16
    assert hamming(original, resaved) <= 6
    assert hamming(original, other) > 12
