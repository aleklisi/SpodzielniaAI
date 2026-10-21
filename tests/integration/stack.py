"""Helpers for tests that run against the dev compose stack (`make up`)."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO_ROOT / "secrets"
DEV_FILES = ("compose.yaml", "compose.dev.yaml")


def compose_image(repository: str) -> str:
    """The pinned ``image:`` reference for ``repository`` in compose.yaml (tests use the same)."""
    text = (REPO_ROOT / "compose.yaml").read_text()
    match = re.search(rf"^\s+image:\s*({re.escape(repository)}:\S+)$", text, re.MULTILINE)
    if match is None:
        raise LookupError(f"no image {repository!r} in compose.yaml")
    return match.group(1)


def require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    probe = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("Docker daemon not reachable")


def secret(name: str) -> str:
    path = SECRETS_DIR / name
    if not path.is_file():
        pytest.skip(f"secret {name!r} missing — run `make secrets && make up`")
    return path.read_text().strip()


def mfa_off() -> bool:
    """`make mfa-off` is in effect: Keycloak skips the OTP step (MFA_REQUIRED=false in `.env`)."""
    env = REPO_ROOT / ".env"
    return env.is_file() and "MFA_REQUIRED=false" in env.read_text().splitlines()


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
