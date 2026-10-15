"""Helpers for tests that run against the dev compose stack (`make up`)."""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO_ROOT / "secrets"
DEV_FILES = ("compose.yaml", "compose.dev.yaml")


def secret(name: str) -> str:
    path = SECRETS_DIR / name
    if not path.is_file():
        pytest.skip(f"secret {name!r} missing — run `make secrets && make up`")
    return path.read_text().strip()


def require_port(port: int, host: str = "127.0.0.1") -> None:
    try:
        with socket.create_connection((host, port), timeout=1):
            return
    except OSError:
        pytest.skip(f"{host}:{port} not reachable — is the dev stack up (`make up`)?")


def compose_files(files: tuple[str, ...], *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose -f <files…> <args…>`` in the repo root (skips without Docker)."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    file_args = [arg for f in files for arg in ("-f", f)]
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["docker", "compose", *file_args, *args],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    """``docker compose`` with the dev file set (compose.yaml + compose.dev.yaml)."""
    return compose_files(DEV_FILES, *args)
