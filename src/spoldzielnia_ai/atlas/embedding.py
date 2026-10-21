"""Image embeddings for the visual atlas (chunk 9.8): local, offline, CPU.

SigLIP base (vision tower) from the read-only ``models`` volume, pinned in
``workers.models``. Vectors are L2-normalised, so cosine similarity is a dot product. Regions
are embedded as crops with a margin, which is what lets "the queen" be found on a frame full of
workers. Nothing here touches the network (``local_files_only``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from PIL.Image import Image

log = get_logger(__name__)

CROP_MARGIN = 0.10  # of the box size, on each side
MIN_CROP_PX = 64


class ImageEmbedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed(self, images: Sequence[Image]) -> list[list[float]]:
        """Blocking: run in a worker thread."""
        ...


@dataclass(frozen=True, slots=True)
class Box:
    x: float
    y: float
    w: float
    h: float


def crop(image: Image, box: Box) -> Image:
    """The box with a margin, at least ``MIN_CROP_PX`` on each side (when the image allows),
    shifted inward rather than cut at the image border."""
    width, height = image.size

    def span(start: float, size: float, total: int) -> tuple[int, int]:
        length = min(float(total), max(size * (1 + 2 * CROP_MARGIN) * total, MIN_CROP_PX))
        centre = (start + size / 2) * total
        low = min(max(centre - length / 2, 0.0), total - length)
        return round(low), round(low + length)

    left, right = span(box.x, box.w, width)
    top, bottom = span(box.y, box.h, height)
    return image.crop((left, top, right, bottom))


class SiglipEmbedder:
    """Loaded once per process, on first use. Thread-safe for the worker's thread pool."""

    def __init__(self, model_dir: Path, batch_size: int = 16) -> None:
        self._dir = model_dir
        self._batch = batch_size
        self._lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None
        self._dimension: int | None = None

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoImageProcessor, SiglipVisionModel
            from transformers.utils import logging as hf_logging

            # The checkpoint also holds the text tower, which the vision model skips: the
            # load report about those keys is expected noise.
            hf_logging.set_verbosity_error()
            torch.set_num_threads(max(1, torch.get_num_threads()))
            processor: Any = AutoImageProcessor.from_pretrained(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType] # nosec B615
                str(self._dir), local_files_only=True
            )
            model: Any = SiglipVisionModel.from_pretrained(  # pyright: ignore[reportUnknownMemberType] # nosec B615
                str(self._dir), local_files_only=True
            )
            model.eval()
            self._processor = processor
            self._model = model
            self._dimension = int(model.config.hidden_size)
            log.info("atlas.image_embedder_loaded", dimension=self._dimension)

    @property
    def dimension(self) -> int:
        self._load()
        assert self._dimension is not None  # noqa: S101 - set by _load
        return self._dimension

    def embed(self, images: Sequence[Image]) -> list[list[float]]:
        import torch

        self._load()
        vectors: list[list[float]] = []
        for start in range(0, len(images), self._batch):
            batch = [image.convert("RGB") for image in images[start : start + self._batch]]
            inputs = self._processor(images=batch, return_tensors="pt")
            with torch.inference_mode():
                pooled = self._model(**inputs).pooler_output
                pooled = torch.nn.functional.normalize(pooled, dim=-1)
            rows: list[list[float]] = pooled.tolist()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            vectors.extend(rows)
        return vectors
