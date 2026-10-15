"""Chunk 1.2b: run the stack smoke script (HTTPS, IdP, hardening, ports, isolation)."""

from __future__ import annotations

import subprocess

import pytest

from tests.integration.stack import REPO_ROOT, require_port

pytestmark = pytest.mark.integration


def test_smoke_script_passes() -> None:
    require_port(443)
    result = subprocess.run(  # noqa: S603 - fixed script path
        [str(REPO_ROOT / "infra" / "scripts" / "smoke.sh")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
