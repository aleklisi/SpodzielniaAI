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
| `spoldzielnia audit-verify` | Re-compute the security audit log hash chain; exit 1 on any break (`docker compose run --rm api audit-verify`) |
| `spoldzielnia bootstrap` | Provision MinIO buckets / Qdrant collections (chunks 1.2b, 4.1) |

## Database & migrations

Two Postgres roles (`infra/postgres/init/10-roles.sh`): `spoldzielnia_owner` owns the schema and
runs migrations only; `spoldzielnia_app` (API runtime) is `NOBYPASSRLS` with DML only, so it cannot
create, alter or drop anything. Models live in `src/spoldzielnia_ai/db/models/`, migrations in
`src/spoldzielnia_ai/db/migrations/versions/` (shipped inside the package).

```bash
make migrate                                   # upgrade the running stack's DB to head
make revision REV=0004_name MSG="what it does"     # autogenerate against the dev DB, then review
```

**Row-Level Security.** Private-zone tables (`private_note`, later attachments/derivatives) have
`ENABLE` + `FORCE ROW LEVEL SECURITY` and an owner policy on `app.current_user_id`. Access them only
through `db.rls.rls_session(factory, user_id)` or the `RlsSessionDep` FastAPI dependency, which set
the id transaction-locally. Any other session sees no rows and cannot insert.

**Audit log.** `security_audit_log` is append-only: the app role has no `UPDATE`/`DELETE`/`TRUNCATE`,
and triggers reject them for every role. A `BEFORE INSERT` trigger chains rows with SHA-256. Write
entries with `audit.service.AuditService` inside the transaction of the action being audited.

Revision ids are explicit and sortable (`NNNN_name`). `/api/readyz` reports `schema_revision` and
fails its `migrations` check until the DB is at the image's head.

## Authentication (OIDC, BFF)

The API is the OAuth confidential client. The browser never sees tokens. `GET /api/auth/login`
(`?return_to=/path`) redirects to Keycloak with PKCE S256. `GET /api/auth/callback` validates the
ID token, provisions the user by `(issuer, subject)` and audits the login. External IdPs (Google
Workspace, Entra ID) are configured as brokers inside Keycloak, so the API trusts one issuer.
The callback then sets `__Host-sid` (HttpOnly) and `__Host-csrf` cookies. Unsafe requests must
send `X-CSRF-Token: <__Host-csrf>` from an allowed `Origin`. `GET /api/me` returns the profile and
`mfa_satisfied`. `POST /api/auth/logout` returns the IdP `logout_url`. Expert/admin endpoints use
`auth.deps.elevated_router(...)`, which requires the role and MFA (403 with a `step_up` URL otherwise).
Dev users: `member1`, `member2` (password), `expert1`, `admin1` (password + TOTP). The password is
in `secrets/dev_user_password`, the TOTP seed in `secrets/dev_totp_seed`.

## Private notebook & media pipeline (Stage 2)

Notes, attachments and derivatives are encrypted per row (envelope encryption, KEK = Docker
secret `kek`) and isolated by Postgres RLS. Uploads (`POST /api/notes/{id}/attachments`) are
validated by content (libmagic), checked by the archive guard, scanned by ClamAV (fail-closed),
stripped of metadata and stored encrypted in MinIO. Links (`POST /api/notes/{id}/links`) pass
SSRF checks and are fetched by `worker-egress`. Celery workers produce derivatives: transcripts
(faster-whisper), parsed documents (Docling) and fetched pages.

```bash
make models        # once: Whisper small + Docling into the `models` volume (needs internet)
make up            # starts worker-media (no internet) and worker-egress too
make models-host   # Whisper tiny + Docling into ./.models for the `ml` tests
```

Host-side file-type tests need libmagic (`brew install libmagic`).

## Test markers

`integration` (Docker services), `ml` (heavy models), `e2e` (running stack). Run a subset with
`uv run pytest -m "not integration"`.
