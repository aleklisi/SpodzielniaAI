"""Image objects of atlas photos (chunk 9.4): metadata-free copies, encrypted per photo.

A photo sent to the atlas is re-encoded from pixels only (A3): no EXIF, no GPS even if the
member kept it on the private original, no ICC, no filename. Three JPEG renditions are stored
(``full`` ≤ 4096 px, ``preview`` ≤ 1600 px, ``thumb`` ≤ 320 px), each encrypted under the
``atlas_image`` row's DEK with its own AAD, so objects can't be swapped between photos or sizes.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.files.preview import FULL_SIDE, PREVIEW_SIDE, Rendition, dhash, render_jpeg
from spoldzielnia_ai.files.storage import new_object_key

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.models import AtlasImage
    from spoldzielnia_ai.files.storage import ObjectStorage

ATLAS_IMAGE = "atlas_image"
THUMB_SIDE = 320
Size = Literal["full", "preview", "thumb"]


def atlas_aad(image_id: UUID, author_id: UUID) -> str:
    return row_aad(ATLAS_IMAGE, image_id, author_id)


def _object_aad(image_id: UUID, author_id: UUID, size: Size) -> str:
    return f"{atlas_aad(image_id, author_id)}|{size}"


@dataclass(frozen=True, slots=True)
class SharedCopies:
    full: Rendition
    preview: Rendition
    thumb: Rendition
    sha256: str  # of the full copy
    phash: str


def make_shared_copies(src: Path, max_pixels: int) -> SharedCopies:
    """Blocking (run in a thread): the renditions of a decrypted private original."""
    full = render_jpeg(src, FULL_SIDE, max_pixels, quality=90)
    preview = render_jpeg(src, PREVIEW_SIDE, max_pixels)
    thumb = render_jpeg(src, THUMB_SIDE, max_pixels, quality=80)
    return SharedCopies(
        full, preview, thumb, hashlib.sha256(full.data).hexdigest(), dhash(src, max_pixels)
    )


@dataclass(frozen=True, slots=True)
class StoredKeys:
    full: str
    preview: str
    thumb: str

    def all(self) -> list[str]:
        return [self.full, self.preview, self.thumb]


class AtlasObjects:
    def __init__(self, cipher: EnvelopeCipher, storage: ObjectStorage) -> None:
        self._cipher = cipher
        self._storage = storage

    async def store(
        self, copies: SharedCopies, image_id: UUID, author_id: UUID, dek: bytes
    ) -> StoredKeys:
        keys: list[str] = []
        try:
            for size, rendition in (
                ("full", copies.full),
                ("preview", copies.preview),
                ("thumb", copies.thumb),
            ):
                sealed = io.BytesIO()
                aad = _object_aad(image_id, author_id, size)  # pyright: ignore[reportArgumentType]

                def seal(
                    data: bytes = rendition.data, out: io.BytesIO = sealed, a: str = aad
                ) -> int:
                    return self._cipher.encrypt_stream(dek, io.BytesIO(data), out, a)

                length = await asyncio.to_thread(seal)
                sealed.seek(0)
                key = new_object_key()
                await self._storage.put(key, sealed, length)
                keys.append(key)
        except BaseException:
            await self.delete(keys)
            raise
        return StoredKeys(*keys)

    async def read(self, image: AtlasImage, size: Size) -> bytes | None:
        key = {
            "full": image.object_key,
            "preview": image.preview_object_key,
            "thumb": image.thumb_object_key,
        }[size]
        if key is None:
            return None
        dek = self._cipher.unwrap(image.dek_wrapped, atlas_aad(image.id, image.author_id))
        sealed = io.BytesIO()
        await self._storage.get(key, sealed)
        sealed.seek(0)
        aad = _object_aad(image.id, image.author_id, size)

        def decrypt() -> bytes:
            return b"".join(self._cipher.decrypt_stream(dek, sealed, aad))

        return await asyncio.to_thread(decrypt)

    async def delete(self, keys: list[str]) -> None:
        for key in keys:
            await self._storage.delete(key)
