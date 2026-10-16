"""``spoldzielnia`` console script — the single container entrypoint.

Subcommands:
    api        run the FastAPI app with uvicorn
    migrate    apply database migrations (Alembic, owner role)
    audit-verify  re-compute the security audit log hash chain; exit 1 on any break
    worker     run a Celery worker for the given queues (2.5)
    models     download/verify the offline ML models (2.5)
    bootstrap  create storage buckets / vector collections — wired up in chunk 1.2b / 4.1
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from spoldzielnia_ai.core.logging import configure_logging, get_logger
from spoldzielnia_ai.core.settings import (
    AuditVerifySettings,
    BootstrapSettings,
    MigrateSettings,
    get_settings,
)

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


def _cmd_migrate(args: argparse.Namespace) -> int:
    from spoldzielnia_ai.db.migrate import upgrade

    settings = MigrateSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level)
    revision: str = args.revision
    log.info("migrate.start", target=revision)
    upgrade(settings.db.migration_url(), revision)
    log.info("migrate.done", target=revision)
    return 0


def _cmd_audit_verify(_args: argparse.Namespace) -> int:
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from spoldzielnia_ai.audit.verify import ChainReport, verify_chain

    settings = AuditVerifySettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level)

    async def run() -> ChainReport:
        engine = create_async_engine(settings.db.app_url())
        try:
            return await verify_chain(engine)
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    for brk in report.breaks:
        log.error("audit_verify.break", id=brk.id, reason=brk.reason)
    log.info(
        "audit_verify.done",
        ok=report.ok,
        rows=report.rows,
        breaks=len(report.breaks),
        head_id=report.head_id,
        head_hash=report.head_hash.hex(),
    )
    return 0 if report.ok else 1


def _cmd_worker(args: argparse.Namespace) -> int:
    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.workers.celery_adapter import worker_main
    from spoldzielnia_ai.workers.jobs import Queue
    from spoldzielnia_ai.workers.runtime import build_worker

    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level, json=settings.environment != "dev")
    queues = [q.strip() for q in str(args.queues).split(",") if q.strip()]
    known = {q.value for q in Queue}
    if not queues or not set(queues) <= known:
        log.error("worker.unknown_queue", queues=queues, known=sorted(known))
        return 2
    log.info("worker.start", queues=queues, concurrency=settings.concurrency)
    worker_main(build_worker(settings), queues, settings.concurrency, settings.log_level)
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    from pathlib import Path

    from spoldzielnia_ai.workers import models

    configure_logging("INFO")
    root = Path(args.root)
    if args.action == "download":
        sizes = [s for s in str(args.whisper).split(",") if s]
        models.download_all(
            root, sizes, docling=not args.no_docling, injection=not args.no_injection
        )
    broken = models.verify(root)
    for path in broken:
        log.error("models.integrity_failure", path=path)
    log.info("models.verified", root=str(root), ok=not broken)
    return 0 if not broken else 1


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    from spoldzielnia_ai.bootstrap import run_bootstrap

    settings = BootstrapSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level)
    run_bootstrap(settings)
    log.info("bootstrap.done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spoldzielnia", description="Spółdzielnia AI backend")
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser("api", help="run the HTTP API (uvicorn)")
    api.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    api.set_defaults(handler=_cmd_api)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head", help="target (default: head)")
    migrate.set_defaults(handler=_cmd_migrate)

    audit_verify = sub.add_parser("audit-verify", help="verify the audit log hash chain")
    audit_verify.set_defaults(handler=_cmd_audit_verify)

    worker = sub.add_parser("worker", help="run a Celery worker")
    worker.add_argument(
        "--queues", required=True, help="comma-separated, e.g. media.transcribe,media.parse"
    )
    worker.set_defaults(handler=_cmd_worker)

    models_cmd = sub.add_parser("models", help="download or verify offline ML models")
    models_cmd.add_argument("action", choices=["download", "verify"])
    models_cmd.add_argument("--root", default="/models", help="models directory (volume)")
    models_cmd.add_argument("--whisper", default="small", help="comma-separated sizes")
    models_cmd.add_argument("--no-docling", action="store_true", help="skip Docling models")
    models_cmd.add_argument(
        "--no-injection", action="store_true", help="skip the prompt-injection classifier"
    )
    models_cmd.set_defaults(handler=_cmd_models)

    bootstrap = sub.add_parser("bootstrap", help="provision buckets and collections")
    bootstrap.set_defaults(handler=_cmd_bootstrap)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Command = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
