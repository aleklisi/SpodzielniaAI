"""Dataset export of the visual atlas (chunk 9.7, A9).

Only APPROVED photos with ACTIVE consent whose author allowed export, of one domain (optionally
narrowed to some labels and a minimum verification date). The archive holds:

- ``images/<opaque>.jpg``: the metadata-free full copies, renamed to ids derived from a
  per-export salt (no atlas ids, no author ids);
- COCO: ``annotations.json`` (``images``, ``annotations`` with pixel ``bbox``, ``categories``);
  YOLO: ``labels/<opaque>.txt`` (``class cx cy w h``, normalised) and ``classes.txt``;
- ``manifest.json``: export id, creator, filters, licence note, and per photo its file, SHA-256,
  label names and the author as an HMAC with the export salt (the same author is recognisable
  within one export, not across exports or back to an account).

The archive is encrypted under the export's DEK. The admin gets a single-use download token
once, at creation; only its SHA-256 is stored.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import secrets
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    AtlasExportFormat,
    AtlasExportStatus,
    AtlasImageStatus,
    ConsentStatus,
)
from spoldzielnia_ai.db.models import AtlasExport, AtlasImage, AtlasRegion, VisualDomain
from spoldzielnia_ai.files.storage import new_object_key

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import AtlasSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage

log = get_logger(__name__)

EXPORT = "atlas_export"
LICENCE = "Do użytku wewnętrznego spółdzielni. Zdjęcia udostępnione za zgodą autorów."


class ExportError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def export_aad(export_id: UUID, created_by: UUID) -> str:
    return row_aad(EXPORT, export_id, created_by)


@dataclass(frozen=True, slots=True)
class Requested:
    export: AtlasExport
    token: str  # shown once


class ExportService:
    """Admin-side operations, in the caller's transaction."""

    def __init__(
        self, session: AsyncSession, cipher: EnvelopeCipher, settings: AtlasSettings
    ) -> None:
        self._session = session
        self._cipher = cipher
        self._settings = settings
        self._audit = AuditService(session)

    async def request(
        self,
        admin_id: UUID,
        export_format: AtlasExportFormat,
        domain_id: UUID,
        label_ids: Sequence[UUID] | None,
        min_verified_at: dt.datetime | None,
    ) -> Requested:
        if await self._session.get(VisualDomain, domain_id) is None:
            raise ExportError("domain_not_found", 404)
        export_id = uuid7()
        token = secrets.token_urlsafe(32)
        key = self._cipher.new_key(export_aad(export_id, admin_id))
        export = AtlasExport(
            id=export_id,
            created_by=admin_id,
            format=export_format,
            domain_id=domain_id,
            label_ids=list(label_ids) if label_ids else None,
            min_verified_at=min_verified_at,
            dek_wrapped=key.wrapped,
            token_sha256=token_hash(token),
            expires_at=dt.datetime.now(dt.UTC)
            + dt.timedelta(seconds=self._settings.export_link_ttl_seconds),
        )
        self._session.add(export)
        await self._session.flush()
        await self._audit.record(
            EventType.ATLAS_EXPORT_REQUESTED,
            admin_id,
            AuditTarget(EXPORT, str(export_id)),
            {
                "format": export_format.value,
                "domain": str(domain_id),
                "labels": len(label_ids or []),
            },
        )
        await self._session.refresh(export)
        return Requested(export, token)

    async def list(self) -> Sequence[AtlasExport]:
        return (
            await self._session.scalars(select(AtlasExport).order_by(AtlasExport.created_at.desc()))
        ).all()

    async def revoked_counts(self, exports: Sequence[AtlasExport]) -> dict[UUID, int]:
        """Photos withdrawn by their authors since each export (or no longer exportable)."""
        ids = {i for e in exports for i in e.image_ids}
        if not ids:
            return {e.id: 0 for e in exports}
        gone = set(
            (
                await self._session.scalars(
                    select(AtlasImage.id).where(
                        AtlasImage.id.in_(ids),
                        (AtlasImage.consent_status != ConsentStatus.ACTIVE)
                        | AtlasImage.allow_export.is_(False),
                    )
                )
            ).all()
        )
        return {e.id: len(gone & set(e.image_ids)) for e in exports}

    async def claim_download(
        self, admin_id: UUID, export_id: UUID, token: str
    ) -> tuple[AtlasExport, str | None]:
        """Check the token and mark the export downloaded (single use), in one transaction.
        Returns the export and its object key, which the caller deletes after commit."""
        export = await self._session.scalar(
            select(AtlasExport).where(AtlasExport.id == export_id).with_for_update()
        )
        if export is None or not hmac.compare_digest(export.token_sha256, token_hash(token)):
            raise ExportError("export_not_found", 404)
        if export.status == AtlasExportStatus.DOWNLOADED:
            raise ExportError("already_downloaded", 410)
        if export.status != AtlasExportStatus.READY or export.object_key is None:
            raise ExportError("not_ready")
        if export.expires_at <= dt.datetime.now(dt.UTC):
            export.status = AtlasExportStatus.EXPIRED
            raise ExportError("expired", 410)
        export.status = AtlasExportStatus.DOWNLOADED
        export.downloaded_at = dt.datetime.now(dt.UTC)
        key, export.object_key = export.object_key, None
        await self._audit.record(
            EventType.ATLAS_EXPORT_DOWNLOADED,
            admin_id,
            AuditTarget(EXPORT, str(export.id)),
            {"manifest_sha256": export.manifest_sha256, "images": export.image_count},
        )
        await self._session.flush()
        return export, key

    async def expire(self) -> list[str]:
        """READY exports past their time: mark EXPIRED, return object keys to delete."""
        rows = (
            await self._session.scalars(
                select(AtlasExport)
                .where(
                    AtlasExport.status == AtlasExportStatus.READY,
                    AtlasExport.expires_at <= func.now(),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        keys: list[str] = []
        for export in rows:
            export.status = AtlasExportStatus.EXPIRED
            if export.object_key:
                keys.append(export.object_key)
            export.object_key = None
        await self._session.flush()
        return keys


# --- Building the archive (worker-core) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Photo:
    image: AtlasImage
    regions: list[tuple[str, float, float, float, float]]  # label name, x, y, w, h


class ExportBuilder:
    def __init__(
        self, factory: SessionFactory, cipher: EnvelopeCipher, storage: ObjectStorage
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._objects = AtlasObjects(cipher, storage)

    async def handle(self, export_id: UUID) -> None:
        async with self._factory() as session:
            export = await session.get(AtlasExport, export_id)
            if export is None or export.status != AtlasExportStatus.PENDING:
                return
            photos = await self._select(session, export)
            domain = await session.get(VisualDomain, export.domain_id)
        try:
            archive, manifest_sha, regions = await self._archive(
                export, photos, domain.name if domain else ""
            )
        except Exception as exc:
            log.error("atlas.export_failed", export=str(export_id), error=type(exc).__name__)
            await self._finish(export_id, error="build_failed")
            return
        dek = self._cipher.unwrap(export.dek_wrapped, export_aad(export.id, export.created_by))
        sealed = io.BytesIO()
        length = self._cipher.encrypt_stream(
            dek, io.BytesIO(archive), sealed, export_aad(export.id, export.created_by)
        )
        sealed.seek(0)
        key = new_object_key()
        await self._storage.put(key, sealed, length)
        stored = await self._finish(
            export_id,
            object_key=key,
            size=len(archive),
            manifest_sha=manifest_sha,
            image_ids=[p.image.id for p in photos],
            regions=regions,
        )
        if not stored:
            await self._storage.delete(key)

    @staticmethod
    async def _select(session: AsyncSession, export: AtlasExport) -> list[_Photo]:
        stmt = select(AtlasImage).where(
            AtlasImage.domain_id == export.domain_id,
            AtlasImage.status == AtlasImageStatus.APPROVED,
            AtlasImage.consent_status == ConsentStatus.ACTIVE,
            AtlasImage.allow_export.is_(True),
        )
        if export.min_verified_at is not None:
            stmt = stmt.where(AtlasImage.verified_at >= export.min_verified_at)
        images = (await session.scalars(stmt.order_by(AtlasImage.verified_at, AtlasImage.id))).all()
        if not images:
            return []
        rows = (
            await session.scalars(
                select(AtlasRegion)
                .where(
                    AtlasRegion.atlas_image_id.in_([i.id for i in images]),
                    AtlasRegion.consent_status == ConsentStatus.ACTIVE,
                )
                .order_by(AtlasRegion.atlas_image_id, AtlasRegion.position)
            )
        ).all()
        labels = await LabelService(session).resolve({r.label_id for r in rows})
        wanted = set(export.label_ids) if export.label_ids else None
        by_image: dict[UUID, list[tuple[str, float, float, float, float]]] = {}
        for r in rows:
            label = labels[r.label_id]
            if wanted is not None and label.id not in wanted:
                continue
            by_image.setdefault(r.atlas_image_id, []).append((label.name, r.x, r.y, r.w, r.h))
        # With a label filter, only photos that show one of the labels.
        return [_Photo(i, by_image[i.id]) for i in images if i.id in by_image]

    async def _archive(
        self, export: AtlasExport, photos: list[_Photo], domain: str
    ) -> tuple[bytes, str, int]:
        salt = secrets.token_bytes(16)

        def opaque(value: UUID) -> str:
            return hmac.new(salt, value.bytes, hashlib.sha256).hexdigest()[:16]

        names = sorted({name for p in photos for name, *_ in p.regions})
        index = {name: i for i, name in enumerate(names)}
        buffer = io.BytesIO()
        coco: dict[str, Any] = {
            "info": {
                "description": f"Atlas spółdzielni: {domain}",
                "version": str(export.id),
                "date_created": dt.datetime.now(dt.UTC).isoformat(),
            },
            "licenses": [{"id": 1, "name": LICENCE}],
            "images": [],
            "annotations": [],
            "categories": [
                {"id": i + 1, "name": name, "supercategory": domain} for i, name in enumerate(names)
            ],
        }
        manifest_photos: list[dict[str, Any]] = []
        region_count = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for number, photo in enumerate(photos, start=1):
                data = await self._objects.read(photo.image, "full")
                if data is None:
                    continue
                stem = opaque(photo.image.id)
                file = f"images/{stem}.jpg"
                archive.writestr(file, data)
                w, h = photo.image.width, photo.image.height
                coco["images"].append(
                    {"id": number, "file_name": file, "width": w, "height": h, "license": 1}
                )
                lines: list[str] = []
                for name, x, y, bw, bh in photo.regions:
                    region_count += 1
                    coco["annotations"].append(
                        {
                            "id": region_count,
                            "image_id": number,
                            "category_id": index[name] + 1,
                            "bbox": [
                                round(x * w, 2),
                                round(y * h, 2),
                                round(bw * w, 2),
                                round(bh * h, 2),
                            ],
                            "area": round(bw * w * bh * h, 2),
                            "iscrowd": 0,
                        }
                    )
                    lines.append(
                        f"{index[name]} {x + bw / 2:.6f} {y + bh / 2:.6f} {bw:.6f} {bh:.6f}"
                    )
                if export.format == AtlasExportFormat.YOLO:
                    archive.writestr(f"labels/{stem}.txt", "\n".join(lines) + "\n")
                manifest_photos.append(
                    {
                        "file": file,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "labels": sorted({name for name, *_ in photo.regions}),
                        "author": opaque(photo.image.author_id),
                        "verified_at": photo.image.verified_at.isoformat()
                        if photo.image.verified_at
                        else None,
                    }
                )
            if export.format == AtlasExportFormat.COCO:
                archive.writestr("annotations.json", json.dumps(coco, ensure_ascii=False, indent=1))
            else:
                archive.writestr("classes.txt", "\n".join(names) + "\n")
            manifest = {
                "export_id": str(export.id),
                "created_by": str(export.created_by),
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "format": export.format.value,
                "domain": domain,
                "filters": {
                    "labels": [str(i) for i in export.label_ids or []],
                    "min_verified_at": export.min_verified_at.isoformat()
                    if export.min_verified_at
                    else None,
                },
                "licence": LICENCE,
                "photos": manifest_photos,
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
            archive.writestr("manifest.json", manifest_bytes)
        return buffer.getvalue(), hashlib.sha256(manifest_bytes).hexdigest(), region_count

    async def _finish(
        self,
        export_id: UUID,
        *,
        error: str | None = None,
        object_key: str | None = None,
        size: int | None = None,
        manifest_sha: str | None = None,
        image_ids: list[UUID] | None = None,
        regions: int = 0,
    ) -> bool:
        async with self._factory() as session, session.begin():
            export = await session.get(AtlasExport, export_id, with_for_update=True)
            if export is None or export.status != AtlasExportStatus.PENDING:
                return False
            if error is not None:
                export.status = AtlasExportStatus.FAILED
                export.error = error
                return False
            export.status = AtlasExportStatus.READY
            export.object_key = object_key
            export.size = size
            export.manifest_sha256 = manifest_sha
            export.image_ids = image_ids or []
            export.image_count = len(image_ids or [])
            export.region_count = regions
            await AuditService(session).record(
                EventType.ATLAS_EXPORTED,
                export.created_by,
                AuditTarget(EXPORT, str(export.id)),
                {
                    "manifest_sha256": manifest_sha,
                    "images": export.image_count,
                    "regions": regions,
                    "format": export.format.value,
                },
            )
        log.info("atlas.exported", export=str(export_id), images=len(image_ids or []))
        return True


async def read_archive(
    cipher: EnvelopeCipher, storage: ObjectStorage, export: AtlasExport
) -> bytes:
    if export.object_key is None:
        raise ExportError("not_ready")
    aad = export_aad(export.id, export.created_by)
    dek = cipher.unwrap(export.dek_wrapped, aad)
    sealed = io.BytesIO()
    await storage.get(export.object_key, sealed)
    sealed.seek(0)
    return b"".join(cipher.decrypt_stream(dek, sealed, aad))
