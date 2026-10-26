"""The organisation's name, logo and colours (chunk 13.3, US-07).

- ``GET /branding`` and the logo/favicon files are public: visitors see the home page, and
  nothing here is sensitive. The files are addressed by the version that stored them, so they are
  cached for a year (``immutable``); a new logo has a new URL.
- ``/admin/branding`` is ADMIN + MFA (``elevated_router``) and CSRF-protected like every unsafe
  request. Writes are limited per admin (``WRITES_PER_HOUR``), previews more loosely.
"""

from __future__ import annotations

import asyncio  # noqa: TC003 - used at runtime (the upload semaphore)
from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - pydantic resolves annotations at runtime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.branding.images import MAX_LOGO_BYTES
from spoldzielnia_ai.branding.service import (
    LOGO_FILES,
    BrandingError,
    BrandingService,
    BrandingView,
    palette_of,
)
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.files.multipart import UploadError, read_upload

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from spoldzielnia_ai.branding.palette import Palette
    from spoldzielnia_ai.core.settings import Settings

log = get_logger(__name__)

router = APIRouter(tags=["branding"])
admin_router = elevated_router(Role.ADMIN, prefix="/admin/branding", tags=["admin-branding"])

WRITES_PER_HOUR = 30
PREVIEWS_PER_HOUR = 600
IMMUTABLE = "public, max-age=31536000, immutable"


def get_branding(request: Request) -> BrandingService:
    state = request.app.state
    return BrandingService(state.session_factory, state.storage, state.scanner)


Service = Annotated[BrandingService, Depends(get_branding)]


def _http(exc: BrandingError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.detail})


async def _limit(request: Request, admin_id: UUID, what: str, per_hour: int) -> None:
    """A fixed hourly window per admin in Redis. Fails open if Redis is down (admins only)."""
    redis: Redis = request.app.state.redis
    key = f"branding:limit:{what}:{admin_id}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, 3600)
    except Exception as exc:
        log.warning("branding.rate_limit_unavailable", error=type(exc).__name__)
        return
    if count > per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited"},
            headers={"Retry-After": "3600"},
        )


async def write_limit(request: Request, principal: CurrentUser) -> None:
    await _limit(request, principal.id, "write", WRITES_PER_HOUR)


async def preview_limit(request: Request, principal: CurrentUser) -> None:
    await _limit(request, principal.id, "preview", PREVIEWS_PER_HOUR)


# --- Schemas ------------------------------------------------------------------------------------

Mode = Literal["light", "dark"]


class BrandingOut(BaseModel):
    org_name: str
    custom_name: bool
    version: int
    # Token → #rrggbb per mode; null: the default colours.
    palette: dict[Mode, dict[str, str]] | None
    logo_url: str | None
    favicon_urls: dict[str, str] | None  # "32", "180"


class AdminBrandingOut(BrandingOut):
    brand: str | None
    accent: str | None
    updated_at: datetime | None


class ColoursIn(BaseModel):
    brand: str = Field(max_length=16)
    accent: str = Field(max_length=16)


class BrandingIn(BaseModel):
    # Null or empty: the default name. Null colours: the default colours.
    org_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=16)
    accent: str | None = Field(default=None, max_length=16)


class CheckOut(BaseModel):
    mode: Mode
    token: str
    against: str
    ratio: float
    required: float


class PreviewOut(BaseModel):
    light: dict[str, str]
    dark: dict[str, str]
    adjusted: list[Literal["brand", "accent"]]
    checks: list[CheckOut]


def _files(settings: Settings, view: BrandingView) -> tuple[str | None, dict[str, str] | None]:
    if view.logo_version is None:
        return None, None
    base = f"{settings.root_path}/branding/files/{view.logo_version}"
    return f"{base}/logo.png", {"32": f"{base}/favicon-32.png", "180": f"{base}/favicon-180.png"}


def _out(request: Request, view: BrandingView) -> BrandingOut:
    logo, favicons = _files(request.app.state.settings, view)
    return BrandingOut(
        org_name=view.org_name,
        custom_name=view.custom_name,
        version=view.version,
        palette=view.palette,  # pyright: ignore[reportArgumentType] - validated tokens
        logo_url=logo,
        favicon_urls=favicons,
    )


def _admin_out(request: Request, view: BrandingView) -> AdminBrandingOut:
    return AdminBrandingOut(
        **_out(request, view).model_dump(),
        brand=view.brand,
        accent=view.accent,
        updated_at=view.updated_at,
    )


def _preview(palette: Palette) -> PreviewOut:
    return PreviewOut(
        light={k: v for k, v in palette.light.items()},
        dark={k: v for k, v in palette.dark.items()},
        adjusted=list(palette.adjusted),
        checks=[
            CheckOut(
                mode=c.mode, token=c.token, against=c.against, ratio=c.ratio, required=c.required
            )
            for c in palette.checks
        ],
    )


# --- Public ---------------------------------------------------------------------------------------


@router.get("/branding", response_model=BrandingOut)
async def get_public_branding(request: Request, service: Service) -> BrandingOut:
    """The look of the cooperative, for every page (signed in or not)."""
    return _out(request, await service.get())


@router.get(
    "/branding/files/{version}/{name}",
    responses={200: {"content": {"image/png": {}}}, 404: {"description": "Not the current logo"}},
)
async def get_branding_file(version: int, name: str, service: Service) -> Response:
    """A file of the current logo (``logo.png``, ``favicon-32.png``, ``favicon-180.png``)."""
    if name not in LOGO_FILES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    data = await service.logo_file(version, name)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(data, media_type="image/png", headers={"Cache-Control": IMMUTABLE})


# --- Administration ------------------------------------------------------------------------------


@admin_router.get("", response_model=AdminBrandingOut)
async def get_admin_branding(request: Request, service: Service) -> AdminBrandingOut:
    return _admin_out(request, await service.get())


@admin_router.post("/preview", response_model=PreviewOut, dependencies=[Depends(preview_limit)])
async def preview_branding(body: ColoursIn) -> PreviewOut:
    """The palette these colours would give, with every contrast ratio. Saves nothing."""
    try:
        return _preview(palette_of(body.brand, body.accent))
    except BrandingError as exc:
        raise _http(exc) from None


@admin_router.put("", response_model=AdminBrandingOut, dependencies=[Depends(write_limit)])
async def update_branding(
    request: Request, body: BrandingIn, service: Service, principal: CurrentUser
) -> AdminBrandingOut:
    try:
        view = await service.update(principal.id, body.org_name, body.brand, body.accent)
    except BrandingError as exc:
        raise _http(exc) from None
    return _admin_out(request, view)


@admin_router.post(
    "/logo",
    response_model=AdminBrandingOut,
    dependencies=[Depends(write_limit)],
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            }
        }
    },
)
async def upload_logo(
    request: Request, service: Service, principal: CurrentUser
) -> AdminBrandingOut:
    """Multipart field ``file``: PNG, JPEG or WebP up to 2 MB. SVG is refused."""
    settings: Settings = request.app.state.settings
    slots: asyncio.Semaphore = request.app.state.upload_slots
    async with slots:
        try:
            upload = await read_upload(
                request.headers.get("content-type"),
                request.stream(),
                max_bytes=MAX_LOGO_BYTES,
                allowed_fields=frozenset(),
                spool_dir=settings.uploads.tmp_dir,
            )
        except UploadError as exc:
            raise HTTPException(status_code=exc.status, detail={"error": exc.code}) from None
        try:
            view = await service.set_logo(
                principal.id, upload.path, upload.filename, upload.content_type
            )
        except BrandingError as exc:
            raise _http(exc) from None
        finally:
            upload.cleanup()
    return _admin_out(request, view)


@admin_router.delete("/logo", response_model=AdminBrandingOut, dependencies=[Depends(write_limit)])
async def delete_logo(
    request: Request, service: Service, principal: CurrentUser
) -> AdminBrandingOut:
    return _admin_out(request, await service.remove_logo(principal.id))


@admin_router.post("/reset", response_model=AdminBrandingOut, dependencies=[Depends(write_limit)])
async def reset_branding(
    request: Request, service: Service, principal: CurrentUser
) -> AdminBrandingOut:
    """Back to the default name, logo and colours."""
    return _admin_out(request, await service.reset(principal.id))
