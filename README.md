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
| `spoldzielnia migrate` | Apply Alembic migrations as the owner role (chunk 1.3) |
| `spoldzielnia bootstrap` | Provision MinIO buckets / Qdrant collections (chunks 1.2b, 4.1) |

## Test markers

`integration` (Docker services), `ml` (heavy models), `e2e` (running stack). Run a subset with
`uv run pytest -m "not integration"`.
