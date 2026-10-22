"""``spoldzielnia`` console script — the single container entrypoint.

Subcommands:
    api        run the FastAPI app with uvicorn
    migrate    apply database migrations (Alembic, owner role)
    audit-verify  re-compute the security audit log hash chain; exit 1 on any break
    worker     run a Celery worker for the given queues (2.5)
    beat       run the Celery beat scheduler (outbox reconciliation, consistency audit, 4.4)
    reindex    re-embed the text index into a new generation, then swap (4.4, 10.3)
    models     download/verify the offline ML models (2.5)
    bootstrap  create the storage bucket (1.2b) and the Qdrant collections (4.1)
    honeypot   inject, list or trace canary blocks (5.4): `docker compose run --rm api honeypot …`
    openapi    write the API's OpenAPI schema (frontend type generation, 6.1)
    llm-egress run the CONNECT-only forward proxy to allowlisted cloud LLM APIs (7.3)
    atlas      `atlas seed-example`: load the example atlas domain, not in prod (9.1);
               `atlas benchmark`: label accuracy of the visual index (9.8)
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


def _cmd_beat(_args: argparse.Namespace) -> int:
    from spoldzielnia_ai.core.settings import BeatSettings
    from spoldzielnia_ai.workers.celery_adapter import beat_main, make_celery

    settings = BeatSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level, json=settings.environment != "dev")
    log.info("beat.start", schedule_file=settings.schedule_file)
    beat_main(make_celery(settings.redis.broker_url()), settings.schedule_file, settings.log_level)
    return 0


def _cmd_reindex(args: argparse.Namespace) -> int:
    import asyncio

    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.vector.reindex import run_reindex

    if not args.yes:
        log.error("reindex.not_confirmed", hint="re-embeds every item with the model: pass --yes")
        return 2
    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level, json=settings.environment != "dev")
    report = asyncio.run(run_reindex(settings))
    return 0 if report.swapped and report.failed == 0 else 1


def _cmd_llm_egress(_args: argparse.Namespace) -> int:
    import asyncio

    from spoldzielnia_ai.core.settings import LlmEgressSettings
    from spoldzielnia_ai.llm.endpoints import allowed_endpoints
    from spoldzielnia_ai.net.connect_proxy import ConnectProxy

    settings = LlmEgressSettings()  # pyright: ignore[reportCallIssue]  # env/secrets
    configure_logging(settings.log_level, json=settings.environment != "dev")
    _, cloud_hosts = allowed_endpoints(settings.llm)
    proxy = ConnectProxy(
        frozenset(cloud_hosts),
        header_timeout=settings.header_timeout_seconds,
        connect_timeout=settings.connect_timeout_seconds,
        idle_timeout=settings.idle_timeout_seconds,
        max_connections=settings.max_connections,
    )

    async def run() -> None:
        server = await proxy.serve(settings.listen_host, settings.listen_port)
        async with server:
            await server.serve_forever()

    asyncio.run(run())
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    from pathlib import Path

    from spoldzielnia_ai.workers import models

    configure_logging("INFO")
    root = Path(args.root)
    if args.action == "download":
        sizes = [s for s in str(args.whisper).split(",") if s]
        models.download_all(
            root,
            sizes,
            docling=not args.no_docling,
            injection=not args.no_injection,
            image_embed=not args.no_image_embed,
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


def _cmd_honeypot(args: argparse.Namespace) -> int:
    import asyncio
    import json
    from pathlib import Path

    from spoldzielnia_ai.shield.honeypot import HoneypotKeyMissing
    from spoldzielnia_ai.shield.tools import honeypot_command

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment != "dev")
    text: str | None = None
    if args.action == "scan":
        text = Path(args.file).read_text() if args.file != "-" else sys.stdin.read()
    try:
        output = asyncio.run(honeypot_command(settings, args.action, int(args.count), text))
    except HoneypotKeyMissing:
        log.error("honeypot.key_missing", hint="mount the honeypot_key secret")
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _cmd_atlas(args: argparse.Namespace) -> int:
    import asyncio

    from spoldzielnia_ai.atlas.seed import seed_example
    from spoldzielnia_ai.db.engine import create_app_engine
    from spoldzielnia_ai.db.session import create_session_factory

    if args.action == "benchmark":
        return _atlas_benchmark()
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment != "dev")
    if args.action == "seed-example" and settings.environment == "prod":
        log.error("atlas.seed_example_refused", reason="example data is not loaded in prod")
        return 2

    async def run() -> int:
        engine = create_app_engine(settings.db)
        try:
            report = await seed_example(create_session_factory(engine))
        finally:
            await engine.dispose()
        log.info(
            "atlas.seed_example",
            domain_id=str(report.domain_id),
            domain_created=report.domain_created,
            labels_created=report.labels_created,
        )
        return 0

    return asyncio.run(run())


def _atlas_benchmark() -> int:
    """Read-only: runs where Qdrant is reachable with the worker's settings (worker-core)."""
    import asyncio
    import json

    from spoldzielnia_ai.atlas.benchmark import run_benchmark
    from spoldzielnia_ai.core.settings import WorkerSettings
    from spoldzielnia_ai.vector.client import make_qdrant

    settings = WorkerSettings()  # pyright: ignore[reportCallIssue]
    configure_logging("INFO")

    async def run() -> dict[str, object]:
        client = make_qdrant(settings.qdrant)
        try:
            return (await run_benchmark(client)).to_json()
        finally:
            await client.close()

    print(json.dumps(asyncio.run(run()), indent=2))
    return 0


def _cmd_openapi(args: argparse.Namespace) -> int:
    """No service is contacted: the app is built with placeholder settings and only its route
    table is read."""
    import json
    from pathlib import Path

    from pydantic import SecretStr

    from spoldzielnia_ai.core.settings import (
        DbSettings,
        OidcSettings,
        RedisSettings,
        SecuritySettings,
        Settings,
    )
    from spoldzielnia_ai.main import create_app

    placeholder = SecretStr("openapi-export")
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        _secrets_dir="/nonexistent",  # pyright: ignore[reportCallIssue]
        environment="dev",
        db=DbSettings(app_password=placeholder),
        redis=RedisSettings(password=placeholder),
        oidc=OidcSettings(client_secret=placeholder),
        security=SecuritySettings(
            session_secret=placeholder,
            kek=SecretStr("MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="),
        ),
    )
    schema = create_app(settings, readiness_checks={}).openapi()
    text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text)
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

    beat = sub.add_parser("beat", help="run the Celery beat scheduler")
    beat.set_defaults(handler=_cmd_beat)

    reindex = sub.add_parser("reindex", help="re-embed the text index from Postgres")
    reindex.add_argument("--yes", action="store_true", help="confirm re-embedding everything")
    reindex.set_defaults(handler=_cmd_reindex)

    models_cmd = sub.add_parser("models", help="download or verify offline ML models")
    models_cmd.add_argument("action", choices=["download", "verify"])
    models_cmd.add_argument("--root", default="/models", help="models directory (volume)")
    models_cmd.add_argument("--whisper", default="small", help="comma-separated sizes")
    models_cmd.add_argument("--no-docling", action="store_true", help="skip Docling models")
    models_cmd.add_argument(
        "--no-injection", action="store_true", help="skip the prompt-injection classifier"
    )
    models_cmd.add_argument(
        "--no-image-embed", action="store_true", help="skip the atlas image-embedding model"
    )
    models_cmd.set_defaults(handler=_cmd_models)

    bootstrap = sub.add_parser("bootstrap", help="provision buckets and collections")
    bootstrap.set_defaults(handler=_cmd_bootstrap)

    honeypot = sub.add_parser("honeypot", help="inject, list or trace canary blocks")
    honeypot.add_argument("action", choices=["inject", "list", "scan"])
    honeypot.add_argument("--count", default=5, type=int, help="canaries to inject (1-20)")
    honeypot.add_argument("--file", default="-", help="text to scan (default: stdin)")
    honeypot.set_defaults(handler=_cmd_honeypot)

    egress = sub.add_parser("llm-egress", help="run the forward proxy for cloud LLM APIs")
    egress.set_defaults(handler=_cmd_llm_egress)

    atlas = sub.add_parser("atlas", help="visual atlas tools")
    atlas.add_argument("action", choices=["seed-example", "benchmark"])
    atlas.set_defaults(handler=_cmd_atlas)

    openapi = sub.add_parser("openapi", help="write the OpenAPI schema (JSON)")
    openapi.add_argument("--out", default="-", help="file to write (default: stdout)")
    openapi.set_defaults(handler=_cmd_openapi)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Command = args.handler
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
