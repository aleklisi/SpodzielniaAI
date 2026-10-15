"""``spoldzielnia`` console script — the single container entrypoint.

Subcommands:
    api        run the FastAPI app with uvicorn
    migrate    apply database migrations (Alembic, owner role) — wired up in chunk 1.3
    bootstrap  create storage buckets / vector collections — wired up in chunk 1.2b / 4.1
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from spoldzielnia_ai.core.logging import configure_logging, get_logger
from spoldzielnia_ai.core.settings import get_settings

log = get_logger(__name__)

Command = Callable[[argparse.Namespace], int]


def _cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "spoldzielnia_ai.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        server_header=False,
        reload=bool(args.reload),
        log_config=None,
    )
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    configure_logging(get_settings().log_level)
    log.info("migrate.noop", reason="no migrations defined yet (chunk 1.3)")
    return 0


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    configure_logging(get_settings().log_level)
    log.info("bootstrap.noop", reason="no resources to provision yet (chunk 1.2b)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spoldzielnia", description="Spółdzielnia AI backend")
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser("api", help="run the HTTP API (uvicorn)")
    api.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    api.set_defaults(handler=_cmd_api)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.set_defaults(handler=_cmd_migrate)

    bootstrap = sub.add_parser("bootstrap", help="provision buckets and collections")
    bootstrap.set_defaults(handler=_cmd_bootstrap)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Command = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
