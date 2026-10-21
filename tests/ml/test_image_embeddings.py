"""Chunk 9.8 (``ml``): the real SigLIP image model, offline (``make models-host``).

Synthetic pictures only, so this checks the plumbing and gross behaviour (same object closer
than different ones, crops close to their whole image), not recognition quality: that is
measured on real atlas photos with ``spoldzielnia atlas benchmark``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from spoldzielnia_ai.atlas.embedding import Box, SiglipEmbedder, crop
from spoldzielnia_ai.core.settings import ModelSettings

pytestmark = pytest.mark.ml

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def embedder() -> SiglipEmbedder:
    model_dir = Path(ModelSettings(root=str(REPO / ".models")).image_embed_dir)
    if not (model_dir / "model.safetensors").is_file():
        pytest.skip(f"no image model in {model_dir}: run `make models-host`")
    return SiglipEmbedder(model_dir)


def scene(shape: str, color: str, at: tuple[int, int] = (300, 200)) -> Image.Image:
    image = Image.new("RGB", (800, 600), (230, 220, 200))
    draw = ImageDraw.Draw(image)
    x, y = at
    box = (x - 80, y - 60, x + 80, y + 60)
    (draw.ellipse if shape == "ellipse" else draw.rectangle)(box, fill=color)
    return image


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_dimension_and_normalisation(embedder: SiglipEmbedder) -> None:
    [vector] = embedder.embed([scene("ellipse", "red")])
    assert len(vector) == embedder.dimension == 768
    assert dot(vector, vector) == pytest.approx(1.0, abs=1e-4)


def test_same_object_closer_than_a_different_one(embedder: SiglipEmbedder) -> None:
    red, red_moved, blue_square = embedder.embed(
        [scene("ellipse", "red"), scene("ellipse", "darkred", (500, 350)), scene("rect", "blue")]
    )
    assert dot(red, red_moved) > dot(red, blue_square) + 0.05


def test_crop_matches_its_object(embedder: SiglipEmbedder) -> None:
    whole = scene("ellipse", "red", (600, 400))
    marked = crop(whole, Box(0.65, 0.55, 0.2, 0.2))
    other = crop(scene("rect", "blue", (600, 400)), Box(0.65, 0.55, 0.2, 0.2))
    reference, same, different = embedder.embed(
        [crop(scene("ellipse", "red", (200, 150)), Box(0.15, 0.15, 0.2, 0.2)), marked, other]
    )
    assert dot(reference, same) > dot(reference, different)
