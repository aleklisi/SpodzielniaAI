from __future__ import annotations

import pytest

from spoldzielnia_ai.cli import build_parser, main


@pytest.mark.parametrize(
    "command", ["api", "migrate", "audit-verify", "bootstrap", "beat", "reindex"]
)
def test_subcommands_registered(command: str) -> None:
    args = build_parser().parse_args([command])
    assert args.command == command
    assert callable(args.handler)


def test_command_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_migrate_targets_head_by_default() -> None:
    assert build_parser().parse_args(["migrate"]).revision == "head"
    assert build_parser().parse_args(["migrate", "0001_core_schema"]).revision == "0001_core_schema"


def test_reindex_refuses_without_confirmation() -> None:
    # Drops both collections: nothing happens (not even settings are loaded) without --yes.
    assert main(["reindex"]) == 2
