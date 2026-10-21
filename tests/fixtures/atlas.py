"""Shared helpers for the visual atlas integration tests (Stage 9)."""

from __future__ import annotations

import datetime as dt
import io
import json
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any

from PIL import Image

from spoldzielnia_ai.atlas.submissions import AtlasPreparer
from spoldzielnia_ai.core.settings import AtlasSettings
from spoldzielnia_ai.files.antivirus import ScanResult
from spoldzielnia_ai.llm.injection_guard import InjectionGuard
from spoldzielnia_ai.notebook.uploads import NoProcessing
from spoldzielnia_ai.privacy.scrubber import ChecksumAnalyzer, PiiScrubber
from tests.fixtures.llm import Call, FakeLLMGateway
from tests.fixtures.pl_ids import make_pesel

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.workers.jobs import AtlasJob, ExportJob
    from tests.fixtures.app import Harness, LoggedIn

MFA: dict[str, Any] = {"amr": ["pwd", "otp"]}
GPS_IFD = 0x8825
PESEL = make_pesel(dt.date(1971, 4, 9), 512)
CLEAN = {"people": False, "faces": False, "documents": False, "plates": False, "addresses": False}


class CleanScanner:
    async def scan(self, src: IO[bytes]) -> ScanResult:
        src.read()
        return ScanResult(clean=True)


@dataclass
class RecordingQueue(NoProcessing):
    atlas: list[AtlasJob] = field(default_factory=lambda: list[Any]())

    exports: list[ExportJob] = field(default_factory=lambda: list[Any]())

    async def atlas_requested(self, job: AtlasJob) -> None:
        self.atlas.append(job)

    async def export_requested(self, job: ExportJob) -> None:
        self.exports.append(job)

    indexed: list[UUID] = field(default_factory=lambda: list[Any]())

    async def atlas_canonical(self, image_id: UUID) -> None:
        self.indexed.append(image_id)


def privacy_reply(flags: dict[str, bool]) -> Any:
    def reply(call: Call) -> str:
        return json.dumps({**CLEAN, **flags})

    return reply


@dataclass
class World:
    h: Harness
    factory: SessionFactory
    queue: RecordingQueue
    llm: FakeLLMGateway
    domain: str
    queen: str
    cell: str

    def preparer(self) -> AtlasPreparer:
        state = self.h.app.state
        return AtlasPreparer(
            self.factory,
            state.cipher,
            self.h.storage,
            PiiScrubber(ChecksumAnalyzer()),
            InjectionGuard(),
            self.llm,
            AtlasSettings(),
            max_pixels=60_000_000,
        )

    async def prepare(self, final_attempt: bool = True) -> bool:
        job = self.queue.atlas.pop()
        return await self.preparer().handle(job, final_attempt=final_attempt)


async def make_world(h: Harness, factory: SessionFactory, queue: RecordingQueue) -> World:
    admin = await h.login(roles=["MEMBER", "ADMIN"], **MFA)

    async def post(path: str, body: dict[str, Any]) -> str:
        response = await admin.client.post(path, json=body, headers=admin.headers)
        assert response.status_code == 201, response.text
        return response.json()["id"]

    domain = await post("/admin/atlas/domains", {"name": "Pszczelarstwo"})
    queen = await post("/atlas/curation/labels", {"domain_id": domain, "name": "Matka pszczela"})
    cell = await post("/atlas/curation/labels", {"domain_id": domain, "name": "Matecznik"})
    return World(h, factory, queue, FakeLLMGateway(reply=privacy_reply({})), domain, queen, cell)


def photo_bytes(
    color: str = "orange", *, gps: bool = True, size: tuple[int, int] = (1200, 800)
) -> bytes:
    """A photo with its own structure per colour (seeded), so perceptual hashes differ."""
    import random

    from PIL import ImageDraw

    exif = Image.Exif()
    exif[0x010F] = "Camera Maker"
    if gps:
        exif[GPS_IFD] = {1: "N", 2: (52.0, 13.0, 0.0)}
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    rng = random.Random(color)
    for _ in range(25):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        shade = rng.randrange(256)
        draw.rectangle(
            (x, y, x + rng.randrange(50, 400), y + rng.randrange(50, 300)), fill=(shade,) * 3
        )
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


async def annotated_photo(
    w: World,
    user: LoggedIn,
    *,
    data: bytes | None = None,
    caption: str = f"Matka z ula 7, właściciel PESEL {PESEL}",
    description: str = "znaczona na zielono",
) -> tuple[str, str]:
    note = await user.client.post("/notes", json={"title": "Pasieka"}, headers=user.headers)
    note_id = note.json()["id"]
    upload = await user.client.post(
        f"/notes/{note_id}/attachments",
        files={"file": ("ul.jpg", data or photo_bytes(), "image/jpeg")},
        data={"keep_location": "true"},
        headers=user.headers,
    )
    att_id = upload.json()["id"]
    saved = await user.client.put(
        f"/notes/{note_id}/attachments/{att_id}/annotations",
        json={
            "domain_id": w.domain,
            "caption": caption,
            "regions": [
                {
                    "label_id": w.queen,
                    "x": 0.1,
                    "y": 0.2,
                    "w": 0.3,
                    "h": 0.25,
                    "description": description,
                },
                {"label_id": w.cell, "x": 0.6, "y": 0.6, "w": 0.2, "h": 0.2},
            ],
            "version": 0,
        },
        headers=user.headers,
    )
    assert saved.status_code == 200, saved.text
    return note_id, att_id


async def submit(user: LoggedIn, note_id: str, att_id: str) -> Any:
    return await user.client.post(
        "/atlas/submissions",
        json={"note_id": note_id, "attachment_id": att_id},
        headers=user.headers,
    )


async def send(user: LoggedIn, sub_id: str, **choices: bool) -> Any:
    body = {"show_author": False, "allow_export": False, **choices}
    return await user.client.post(
        f"/atlas/submissions/{sub_id}/send", json=body, headers=user.headers
    )


async def sent_photo(w: World, user: LoggedIn, color: str = "orange", **choices: bool) -> str:
    """An annotated photo prepared and sent to review; returns the atlas image id."""
    note_id, att_id = await annotated_photo(w, user, data=photo_bytes(color))
    response = await submit(user, note_id, att_id)
    assert response.status_code == 202, response.text
    assert await w.prepare() is False
    sent = await send(user, response.json()["id"], **choices)
    assert sent.status_code == 200, sent.text
    return sent.json()["atlas"]["id"]


async def decide(
    expert: LoggedIn, image_id: str, decision: str, version: int, comment: str | None = None
) -> Any:
    return await expert.client.post(
        f"/atlas/review/{image_id}/decision",
        json={"decision": decision, "version": version, "comment": comment},
        headers=expert.headers,
    )


async def approve_all(h: Harness, image_id: str, quorum: int = 1) -> None:
    """Enough distinct experts (with MFA) approve the photo's current version."""
    for _ in range(quorum):
        expert = await h.login(roles=["MEMBER", "EXPERT"], **MFA)
        version = (await expert.client.get(f"/atlas/review/{image_id}")).json()["version"]
        response = await decide(expert, image_id, "APPROVE", version)
        assert response.status_code == 200, response.text
