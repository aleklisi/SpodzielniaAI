"""Typed adapter around ``mutagen`` (untyped, conventions §2.2): strip audio/video tags."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import mutagen

if TYPE_CHECKING:
    from pathlib import Path


class AudioMetadataError(Exception):
    pass


def strip_tags(path: Path) -> bool:
    """Remove every tag (ID3, Vorbis comments, MP4 atoms…) in place. Returns True if the file
    had tags. Raises ``AudioMetadataError`` if mutagen cannot parse the file."""
    try:
        media = cast("Any", mutagen.File(str(path)))  # pyright: ignore[reportPrivateImportUsage, reportUnknownMemberType]
    except mutagen.MutagenError as exc:  # pyright: ignore[reportPrivateImportUsage]
        raise AudioMetadataError(str(exc)) from exc
    if media is None:
        raise AudioMetadataError("unrecognised audio container")
    had_tags = bool(media.tags)
    if media.tags is not None:
        media.delete()
    return had_tags


def has_tags(path: Path) -> bool:
    media = cast("Any", mutagen.File(str(path)))  # pyright: ignore[reportPrivateImportUsage, reportUnknownMemberType]
    return media is not None and bool(media.tags)
