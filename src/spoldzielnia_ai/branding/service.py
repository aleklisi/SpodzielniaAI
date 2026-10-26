"""The organisation's branding: read by everyone, changed by administrators (chunks 13.1-13.2).

Every change is one transaction with its audit entry and raises ``version`` (cache keys and
the immutable file URLs depend on it). Logo files are written to object storage before the row
that points at them commits, and the previous version's files are deleted after it: a reader
never gets a URL whose files are missing. The files are public (the home page shows them to
visitors), so unlike notebook attachments they are stored unencrypted.
"""

from __future__ import annotations

import asyncio
import io
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.branding.images import LogoFiles, render_logo
from spoldzielnia_ai.branding.palette import (
    ContrastUnreachable,
    Palette,
    derive,
    is_valid_tokens,
    normalize_hex,
)
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.models import Branding
from spoldzielnia_ai.files.antivirus import ScannerUnavailable
from spoldzielnia_ai.files.validation import FileRejected

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.antivirus import VirusScanner
    from spoldzielnia_ai.files.storage import ObjectStorage

log = get_logger(__name__)

DEFAULT_NAME = "Spółdzielnia AI"
MAX_NAME = 60
TARGET = AuditTarget("branding", "1")


class BrandingError(Exception):
    def __init__(self, code: str, status: int = 422, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class BrandingView:
    org_name: str  # the default when not set
    custom_name: bool
    brand: str | None
    accent: str | None
    palette: dict[str, dict[str, str]] | None  # None: the default colours
    logo_version: int | None
    version: int
    updated_at: datetime | None


def logo_key(version: int, name: str) -> str:
    return f"branding/{version}/{name}"


LOGO_FILES = ("logo.png", "favicon-32.png", "favicon-180.png")


def clean_name(raw: str | None) -> str | None:
    """The organisation's name as stored: NFC, trimmed, 1-60 characters, no control, format or
    bidi characters (Unicode category C). ``None`` or empty: the default name."""
    if raw is None:
        return None
    name = unicodedata.normalize("NFC", raw).strip()
    if not name:
        return None
    if len(name) > MAX_NAME:
        raise BrandingError("name_too_long", max=MAX_NAME)
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        raise BrandingError("name_invalid")
    return name


def palette_of(brand: str, accent: str) -> Palette:
    try:
        return derive(normalize_hex(brand), normalize_hex(accent))
    except ContrastUnreachable as exc:
        raise BrandingError("contrast_unreachable", which=exc.which, mode=exc.mode) from None
    except ValueError:
        raise BrandingError("invalid_colour") from None


def _view(row: Branding | None) -> BrandingView:
    if row is None:
        return BrandingView(DEFAULT_NAME, False, None, None, None, None, 0, None)
    palette = row.palette if is_valid_tokens(row.palette) else None
    return BrandingView(
        org_name=row.org_name or DEFAULT_NAME,
        custom_name=row.org_name is not None,
        brand=row.brand_hex if palette else None,
        accent=row.accent_hex if palette else None,
        palette=palette,
        logo_version=row.logo_version,
        version=row.version,
        updated_at=row.updated_at,
    )


class BrandingService:
    def __init__(
        self, sessions: SessionFactory, storage: ObjectStorage, scanner: VirusScanner
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._scanner = scanner

    async def get(self) -> BrandingView:
        async with self._sessions() as session:
            return _view(await session.get(Branding, 1))

    async def _locked(self, session: AsyncSession) -> Branding:
        """The row, created if missing and locked for this transaction."""
        await session.execute(insert(Branding).values(id=1).on_conflict_do_nothing())
        row = await session.scalar(select(Branding).where(Branding.id == 1).with_for_update())
        if row is None:  # pragma: no cover - the insert above guarantees it
            raise RuntimeError("branding row missing")
        return row

    def _touch(self, row: Branding, actor: UUID) -> None:
        row.version += 1
        row.updated_by = actor
        row.updated_at = datetime.now(UTC)

    async def update(
        self, actor: UUID, org_name: str | None, brand: str | None, accent: str | None
    ) -> BrandingView:
        """Set the name (``None``: the default) and the colours (both, or neither: the default)."""
        name = clean_name(org_name)
        if (brand is None) != (accent is None):
            raise BrandingError("colours_together")
        palette = palette_of(brand, accent) if brand is not None and accent is not None else None
        async with self._sessions() as session, session.begin():
            row = await self._locked(session)
            new_brand = normalize_hex(brand) if brand is not None else None
            new_accent = normalize_hex(accent) if accent is not None else None
            fields = [
                field
                for field, changed in (
                    ("org_name", row.org_name != name),
                    ("colours", (row.brand_hex, row.accent_hex) != (new_brand, new_accent)),
                )
                if changed
            ]
            if fields:
                row.org_name = name
                row.brand_hex, row.accent_hex = new_brand, new_accent
                row.palette = palette.tokens() if palette else None
                self._touch(row, actor)
                await AuditService(session).record(
                    EventType.BRANDING_CHANGED,
                    actor,
                    TARGET,
                    {
                        "fields": list(fields),
                        "version": row.version,
                        "adjusted": list(palette.adjusted) if palette else [],
                    },
                )
            return _view(row)

    async def set_logo(
        self, actor: UUID, path: Path, filename: str, content_type: str | None
    ) -> BrandingView:
        """Scan, validate and re-encode the upload, store its files, then point the row at them."""
        try:
            with path.open("rb") as handle:
                verdict = await self._scanner.scan(handle)
        except ScannerUnavailable as exc:
            log.error("branding.scanner_unavailable", error=str(exc))
            raise BrandingError("scanner_unavailable", 503) from None
        if not verdict.clean:
            raise BrandingError("infected")
        try:
            files: LogoFiles = await asyncio.to_thread(render_logo, path, filename, content_type)
        except FileRejected as exc:
            raise BrandingError(exc.code, exc.status) from None

        async with self._sessions() as session, session.begin():
            row = await self._locked(session)
            previous = row.logo_version
            self._touch(row, actor)
            for name, data in files.files().items():
                await self._storage.put(logo_key(row.version, name), io.BytesIO(data), len(data))
            row.logo_version = row.version
            row.logo_sha256 = files.sha256
            await AuditService(session).record(
                EventType.BRANDING_CHANGED,
                actor,
                TARGET,
                {"fields": ["logo"], "version": row.version, "logo_sha256": files.sha256},
            )
            view = _view(row)
        await self._forget(previous)
        return view

    async def remove_logo(self, actor: UUID) -> BrandingView:
        async with self._sessions() as session, session.begin():
            row = await self._locked(session)
            previous = row.logo_version
            if previous is None:
                return _view(row)
            row.logo_version = row.logo_sha256 = None
            self._touch(row, actor)
            await AuditService(session).record(
                EventType.BRANDING_CHANGED,
                actor,
                TARGET,
                {"fields": ["logo"], "version": row.version},
            )
            view = _view(row)
        await self._forget(previous)
        return view

    async def reset(self, actor: UUID) -> BrandingView:
        """Back to the default look. The row stays, so ``version`` keeps growing (caches)."""
        async with self._sessions() as session, session.begin():
            row = await self._locked(session)
            previous = row.logo_version
            row.org_name = row.brand_hex = row.accent_hex = None
            row.palette = None
            row.logo_version = row.logo_sha256 = None
            self._touch(row, actor)
            await AuditService(session).record(
                EventType.BRANDING_RESET, actor, TARGET, {"version": row.version}
            )
            view = _view(row)
        await self._forget(previous)
        return view

    async def logo_file(self, version: int, name: str) -> bytes | None:
        """A stored file of the **current** logo; older versions are gone (``None``)."""
        if name not in LOGO_FILES:
            return None
        current = await self.get()
        if current.logo_version != version:
            return None
        out = io.BytesIO()
        try:
            await self._storage.get(logo_key(version, name), out)
        except Exception as exc:
            log.error("branding.logo_missing", version=version, file=name, error=type(exc).__name__)
            return None
        return out.getvalue()

    async def _forget(self, version: int | None) -> None:
        """Best effort: a leftover file is harmless (only the current version is served)."""
        if version is None:
            return
        for name in LOGO_FILES:
            try:
                await self._storage.delete(logo_key(version, name))
            except Exception as exc:
                log.warning(
                    "branding.logo_cleanup_failed", version=version, error=type(exc).__name__
                )
