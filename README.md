# Spółdzielnia AI

Cooperative knowledge platform: encrypted private notebooks, an expert-governed wiki with
block-level provenance and consent, and a RAG chat protected by an anti-distillation shield.

- Specification: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/SECURITY.md`](docs/SECURITY.md)
- Implementation plan: [`PLAN.md`](PLAN.md), [`docs/implementation/`](docs/implementation/00_OVERVIEW.md)
- Agent rules: [`AGENTS.md`](AGENTS.md)

## Quickstart (backend, no Docker)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                                   # install runtime + dev dependencies
uv run ruff check --fix . && uv run ruff format .
uv run pyright                            # strict mode, must be 0 errors
uv run pytest                             # unit tests; integration/ml need Docker/models
```

Run the API locally (Postgres/Redis are provided by Docker Compose from chunk 1.2a on):

```bash
cp .env.example .env
export SPOLDZIELNIA_DB__APP_PASSWORD=dev SPOLDZIELNIA_REDIS__PASSWORD=dev \
       SPOLDZIELNIA_OIDC__CLIENT_SECRET=dev SPOLDZIELNIA_SECURITY__SESSION_SECRET=dev
uv run spoldzielnia api --reload          # GET /healthz, /readyz, /docs (non-prod)
```

## Docker stack (macOS dev)

Requires Docker Desktop (≥ 12 GB RAM recommended).

```bash
make up          # generate ./secrets, build the API image, start everything, wait for health
make trust-cert  # once: trust Caddy's local CA so https://spoldzielnia.localhost is green
make smoke       # HTTPS, IdP discovery, container hardening, ports, network isolation
make test-int    # integration tests against the running stack
make down        # stop (keeps data) · make reset: stop and delete all volumes
```

| URL | What |
|---|---|
| `https://spoldzielnia.localhost/api/docs` | API (OpenAPI UI, non-prod) |
| `https://auth.spoldzielnia.localhost` | Keycloak (realm `spoldzielnia`) |

Dev users `member1`, `member2`, `expert1`, `admin1` share the password in
`secrets/dev_user_password`. `expert1`/`admin1` have TOTP configured with the raw key in
`secrets/dev_totp_seed` (Keycloak stores the raw string; authenticator apps need its base32
encoding). Debug ports (`127.0.0.1` only): Postgres 5432, Redis 6379, Qdrant 6333 (TLS),
MinIO 9000/9001, ClamAV 3310, Mailpit 8025 (`--profile tools`).

## Configuration

`spoldzielnia_ai.core.settings.Settings` reads:

- **Environment** (`SPOLDZIELNIA_` prefix, `__` for nested groups), e.g. `SPOLDZIELNIA_DB__HOST`.
- **Docker secrets** from `/run/secrets/<group>__<field>`, e.g. `/run/secrets/db__app_password`.
  Secrets are typed `SecretStr` and never rendered in `repr()` or logs.

Host defaults are compose service names (`postgres`, `redis`, `qdrant`, `minio`, `llm`).

## CLI

`spoldzielnia` is the single container entrypoint:

| Command | Purpose |
|---|---|
| `spoldzielnia api [--reload]` | Run the FastAPI app (uvicorn, proxy headers) |
| `spoldzielnia migrate [revision]` | Apply Alembic migrations (default `head`) as the owner role |
| `spoldzielnia bootstrap` | Provision MinIO buckets / Qdrant collections (chunks 1.2b, 4.1) |

## Database & migrations

Two Postgres roles (`infra/postgres/init/10-roles.sh`): `spoldzielnia_owner` owns the schema and
runs migrations only; `spoldzielnia_app` (API runtime) is `NOBYPASSRLS` with DML only, so it cannot
create, alter or drop anything. Models live in `src/spoldzielnia_ai/db/models/`, migrations in
`src/spoldzielnia_ai/db/migrations/versions/` (shipped inside the package).

```bash
make migrate                                   # upgrade the running stack's DB to head
make revision REV=0002_rls MSG="row level security"   # autogenerate against the dev DB, then review
```

Revision ids are explicit and sortable (`NNNN_name`). `/api/readyz` reports `schema_revision` and
fails its `migrations` check until the DB is at the image's head.

## Test markers

`integration` (Docker services), `ml` (heavy models), `e2e` (running stack). Run a subset with
`uv run pytest -m "not integration"`.
