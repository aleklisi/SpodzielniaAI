from __future__ import annotations

import pytest

from spoldzielnia_ai.cli import build_parser


@pytest.mark.parametrize("command", ["api", "migrate", "bootstrap"])
def test_subcommands_registered(command: str) -> None:
    args = build_parser().parse_args([command])
    assert args.command == command
    assert callable(args.handler)


def test_command_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
