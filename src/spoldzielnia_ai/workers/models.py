"""Offline model provisioning for the media workers (``spoldzielnia models …``, ``make models``).

Runs once with internet access (``models-init``, profile ``tools``) and fills the ``models``
volume. The workers mount it read-only with ``HF_HUB_OFFLINE=1`` and have no network to download
from anyway.

- Whisper (CTranslate2, ``Systran/faster-whisper-*``): pinned commit, and the weights are
  checked against SHA-256 values pinned **here** (not taken from the hub at download time).
- Docling: its own downloader (layout, TableFormer, RapidOCR), without the unused enrichment
  models.
- Afterwards ``MANIFEST.sha256`` records every file. ``verify`` re-checks the volume
  (``spoldzielnia models verify``).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

log = get_logger(__name__)

MANIFEST = "MANIFEST.sha256"

# size → (repository commit, {LFS file: sha256})
WHISPER: dict[str, tuple[str, dict[str, str]]] = {
    "tiny": (
        "d90ca5fe260221311c53c58e660288d3deb8d356",
        {"model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"},
    ),
    "small": (
        "536b0662742c02347bc0e980a01041f333bce120",
        {"model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"},
    ),
    "large-v3": (
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        {"model.bin": "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"},
    ),
}
WHISPER_FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]

# Prompt-injection classifier (3.3): the model LLM-Guard's PromptInjection scanner uses.
INJECTION_REPO = "protectai/deberta-v3-base-prompt-injection-v2"
INJECTION_REVISION = "90c9989b1a342275dd0d1a95aad283c04e075671"
INJECTION_HASHES = {
    "model.safetensors": "6521cb8d0ac08148c81464899c424e6148fcc62befa371089fa4061d8b6e0424",
    "spm.model": "c679fbf93643d19aab7ee10c0b99e460bdbc02fedf34b92b05af343b4af586fd",
}
INJECTION_FILES = [
    "config.json",
    "model.safetensors",
    "spm.model",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
]
INJECTION_DIR = "injection/deberta-v3-base-prompt-injection-v2"


class ModelIntegrityError(Exception):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_whisper(root: Path, size: str) -> Path:
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    if size not in WHISPER:
        raise ValueError(f"unknown whisper size {size!r}; pinned: {sorted(WHISPER)}")
    revision, hashes = WHISPER[size]
    target = root / "whisper" / size
    snapshot_download(
        f"Systran/faster-whisper-{size}",
        revision=revision,
        local_dir=target,
        allow_patterns=WHISPER_FILES,
    )
    for name, expected in hashes.items():
        actual = sha256_file(target / name)
        if actual != expected:
            raise ModelIntegrityError(f"whisper-{size}/{name}: sha256 {actual} != {expected}")
    log.info("models.whisper_ready", size=size, revision=revision)
    return target


def download_injection(root: Path) -> Path:
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    target = root / INJECTION_DIR
    snapshot_download(
        INJECTION_REPO,
        revision=INJECTION_REVISION,
        local_dir=target,
        allow_patterns=INJECTION_FILES,
    )
    for name, expected in INJECTION_HASHES.items():
        actual = sha256_file(target / name)
        if actual != expected:
            raise ModelIntegrityError(f"{INJECTION_REPO}/{name}: sha256 {actual} != {expected}")
    log.info("models.injection_ready", revision=INJECTION_REVISION)
    return target


def download_docling(root: Path) -> Path:
    from docling.utils.model_downloader import download_models

    target = root / "docling"
    download_models(
        output_dir=target,
        with_layout=True,
        with_tableformer=True,
        with_code_formula=False,
        with_picture_classifier=False,
        with_rapidocr=True,
        with_easyocr=False,
    )
    log.info("models.docling_ready")
    return target


def write_manifest(root: Path) -> int:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != MANIFEST and ".cache" not in path.parts
    ]
    (root / MANIFEST).write_text("\n".join(lines) + "\n")
    return len(lines)


def verify(root: Path) -> list[str]:
    """Paths whose content differs from the manifest (or are missing). Empty: intact."""
    manifest = root / MANIFEST
    if not manifest.is_file():
        return [MANIFEST]
    broken: list[str] = []
    for line in manifest.read_text().splitlines():
        expected, _, relative = line.partition("  ")
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            broken.append(relative)
    return broken


def download_all(
    root: Path, whisper_sizes: list[str], docling: bool, injection: bool = True
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for size in whisper_sizes:
        download_whisper(root, size)
    if docling:
        download_docling(root)
    if injection:
        download_injection(root)
    count = write_manifest(root)
    log.info("models.manifest_written", files=count)
