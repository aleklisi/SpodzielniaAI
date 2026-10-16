# Stage 1 — Foundation, OIDC/OAuth2 & Postgres RLS

**Goal:** a runnable, typed FastAPI backend with delegated OIDC login, hardened cookie sessions,
and engine-enforced tenant isolation for private data, **running as a Docker compose web app
behind HTTPS** (see [DEPLOYMENT_DOCKER.md](DEPLOYMENT_DOCKER.md)).
**Spec refs:** `PLAN.md` Etap 1 · `AGENTS.md` §3.1 · `ARCHITECTURE.md` §1

---

## Chunk 1.1 — Repository bootstrap & toolchain

**Deliverables**
- `uv init --package --name spoldzielnia-ai` (src layout, Python `>=3.12`, `.python-version`).
- Runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`,
  `sqlalchemy[asyncio]>=2`, `asyncpg`, `alembic`, `structlog`, `httpx`.
- Dev deps (`uv add --dev`): `ruff`, `pyright`, `pytest`, `pytest-asyncio`, `pytest-cov`,
  `respx`, `fakeredis`, `testcontainers[postgres,redis]`, `freezegun`.
- `pyproject.toml`:
  - `[tool.ruff]` — `line-length = 100`, rules `E,F,W,I,UP,B,S,ASYNC,SIM,RUF,PT,TC`.
    (`S` = bandit rules; per-file ignore `S101` in `tests/`.)
  - `[tool.pyright]` — `typeCheckingMode = "strict"`, `pythonVersion = "3.12"`,
    `include = ["src", "tests"]`, `stubPath = "typings"`.
  - `[tool.pytest.ini_options]` — `asyncio_mode = "auto"`, `--strict-markers`,
    markers `integration`, `ml`, `e2e`.
- `src/spoldzielnia_ai/core/settings.py`: `Settings(BaseSettings)` with nested groups
  (`db`, `redis`, `oidc`, `qdrant`, `s3`, `llm`, `security`). Secrets typed as `SecretStr`.
  **Container-ready:** `secrets_dir="/run/secrets"` (Docker secrets), all hosts/URLs configurable
  (defaults are compose service names, never `localhost`).
- `core/logging.py`: structlog JSON logs to **stdout** + a processor that **redacts** keys
  (`authorization`, `cookie`, `token`, `password`, `content`).
- `main.py`: `create_app()` factory, `/healthz` (liveness) and `/readyz` (checks DB, Redis).
  `root_path` from settings (`/api` behind Caddy).
- `cli.py`: `spoldzielnia` console script (`[project.scripts]`) with subcommands `api`, `migrate`,
  `bootstrap` (more are added in later stages). This is the single container entrypoint.
- `.gitignore` (incl. `secrets/`), `.dockerignore`, `.env.example`, `README.md` (quickstart),
  `.pre-commit-config.yaml` (ruff, pyright).
- GitHub Actions `ci.yml`: ruff → pyright → pytest (unit), then integration job with Docker.

**Tests**
- `tests/unit/test_health.py`: `/healthz` returns 200 with `httpx.AsyncClient(ASGITransport)`.
- `tests/unit/test_settings.py`: secrets aren't rendered in `repr()`, missing required env fails fast,
  and secrets load from a temp `secrets_dir`.
- `tests/unit/test_logging.py`: sensitive keys are redacted.

**Acceptance:** all three verification commands pass on a clean checkout.

---

## Chunk 1.2a — Containerised infrastructure services, networks & secrets

Root `compose.yaml` (base) + `compose.dev.yaml` (Mac overrides). Configs live in `infra/`.

**Services**
| Service | Image (pinned digest) | Network | Notes |
|---|---|---|---|
| `postgres` | `postgres:16` | backend | Init scripts read Docker secrets and create `spoldzielnia_owner`, `spoldzielnia_app` (`NOBYPASSRLS`), `keycloak` roles + DBs |
| `qdrant` | `qdrant/qdrant` | backend | API key from secret, TLS via `infra/qdrant/config.yaml` + internal CA |
| `redis` | `redis:7` | backend | `requirepass` from secret, `appendonly yes` |
| `keycloak` | `quay.io/keycloak/keycloak` | public + backend | Postgres-backed (DB `keycloak`); dev: `start-dev --import-realm` |
| `clamav` | `clamav/clamav` | backend (+ egress for `freshclam`) | clamd on 3310, healthcheck waits for signature DB; verify arm64 tag on Mac |
| `minio` | `minio/minio` | backend | **(gap G2)** bucket created by `bootstrap` (1.2b), SSE enabled |
| `mailpit` | profile `tools` | public | Keycloak email flows (dev only) |

**Networks** (fixed subnets): `public` (bridge, only proxy + Keycloak), `edge` (`internal: true`,
proxy ↔ api/frontend), `backend` (`internal: true`, data + workers), `egress` (bridge, used from
Stage 2 by `worker-egress` and by ClamAV `freshclam`).

**Secrets:** `infra/scripts/init-secrets.sh` (`make secrets`) generates `./secrets/*` (mode 600,
gitignored). Compose `secrets:` mounts them at `/run/secrets`. No secret appears in env vars.
The full list is in DEPLOYMENT_DOCKER §5.

- `infra/scripts/gen-dev-certs.sh`: internal CA + Qdrant server cert (openssl), into a volume.
- `infra/keycloak/realm-spoldzielnia.json`:
  - client `spoldzielnia-backend` (confidential, standard flow, PKCE `S256` required), redirect URI
    `https://spoldzielnia.localhost/api/auth/callback`, web origin `https://spoldzielnia.localhost`.
  - realm roles `MEMBER`, `EXPERT`, `ADMIN`, plus a protocol mapper that puts roles in the `roles` claim.
  - test users (dev realm only): `member1`, `member2`, `expert1` (OTP configured), `admin1` (OTP configured).
  - Authentication flow that emits `acr=mfa` / `amr=["pwd","otp"]` when OTP was used.
- Every service has a healthcheck. **No ports are published in `compose.yaml`.** `compose.dev.yaml`
  publishes debug ports on `127.0.0.1` only (5432, 6379, 6333, 9001 MinIO console, 8025 Mailpit).
  Docker doesn't publish ports for containers attached only to `internal` networks, so the dev
  override also attaches those data services to a dev-only `devhost` bridge network. That network
  never exists in prod.
- `Makefile`: `secrets`, `up`, `down`, `reset` (drops volumes), `logs`, `ps`, `test`, `test-int`.

**Tests**
- `tests/integration/test_infra_smoke.py` (marker `integration`): connects to each service via the
  dev debug ports.

**Acceptance:** `make secrets && make up` on the Mac gives all services healthy within 3 min.
Qdrant rejects requests without the API key. `docker compose config` shows no plaintext secrets.

---

## Chunk 1.2b — Backend image, reverse proxy (Caddy) & HTTPS single origin

**Backend image** `docker/backend.Dockerfile` (multi-stage uv build, sketch in DEPLOYMENT_DOCKER §2):
- Target `api` only for now. `worker-media` / `worker-core` targets are added in 2.5 / 3.2.
- `python:3.12-slim-bookworm`, `uv sync --locked --no-dev`, bytecode compiled, BuildKit cache
  mounts, non-root uid 10001, `tini` entrypoint → `spoldzielnia`.
- `docker-bake.hcl` with target `api` and platforms `linux/arm64` (Mac) + `linux/amd64` (server).

**Compose services added**
- `migrate`: `spoldzielnia migrate` (Alembic as `spoldzielnia_owner`), one-shot. Before 1.3 it's a no-op.
- `bootstrap`: one-shot: creates MinIO bucket(s) and later Qdrant collections (4.1). Idempotent.
- `api`: `spoldzielnia api` → uvicorn `--factory --proxy-headers --forwarded-allow-ips=<edge subnet>`.
  `depends_on: migrate (completed), bootstrap (completed), postgres/redis (healthy)`. Hardening anchor
  applied (read-only rootfs, `cap_drop: ALL`, `no-new-privileges`).
- `proxy`: Caddy, the **only** service with published ports (80/443).
  - `infra/caddy/Caddyfile`: `spoldzielnia.localhost` → `handle_path /api/*` → `api:8000`;
    `/` → temporary static placeholder page until the frontend exists (6.1);
    `auth.spoldzielnia.localhost` → `keycloak:8080` (`/admin` restricted to 127.0.0.1 / allow-list).
  - Dev: `tls internal` (gap G21). `make trust-cert` adds Caddy's root CA to the macOS keychain.
  - Network alias `auth.spoldzielnia.localhost` on `edge`, so `api` reaches the issuer through the
    same URL as the browser (gap G23).
- `compose.dev.yaml`: `build:` contexts and `develop.watch` (sync `src/`, uvicorn `--reload`).

**Smoke script** `infra/scripts/smoke.sh` (`make smoke`), also used by CI and the Docker gate:
1. `https://spoldzielnia.localhost/api/healthz` → 200 and `/api/readyz` → 200 (with Caddy CA).
2. `https://auth.spoldzielnia.localhost/realms/spoldzielnia/.well-known/openid-configuration` → 200.
3. `docker compose exec api id -u` ≠ 0. Writing to `/app` fails (read-only rootfs).
4. Only the `proxy` container has published ports (`docker compose ps --format json`).
5. Network isolation: `docker compose exec api python -c "import socket; socket.create_connection(('1.1.1.1', 443), 3)"`
   **fails** (API has no internet), and the same check from a throwaway container on `backend` fails
   too. `postgres`/`redis` aren't reachable from the host except via the dev debug ports bound to
   `127.0.0.1`.

**Acceptance:** on the Mac, `make up` gets you `https://spoldzielnia.localhost/api/docs` in the
browser with a trusted certificate. `make smoke` is green. CI `docker` job builds the image and runs
the smoke script.

---

## Chunk 1.3 — Database layer, roles & core models

**DB roles (gap G8)**
- `spoldzielnia_owner`: owns schema/tables, runs Alembic. Never used at runtime.
- `spoldzielnia_app`: `LOGIN NOSUPERUSER NOBYPASSRLS`, granted DML on tables only.
- Settings hold two DSNs: `DB_MIGRATION_URL` and `DB_APP_URL`.

**Engine/session** (`db/engine.py`, `db/session.py`)
- Async engine (`asyncpg`), `async_sessionmaker(expire_on_commit=False)`.
- Base model with `id: UUID` (uuid7), `created_at`, `updated_at` (server defaults).

**Models** (`db/models/`). SQLAlchemy 2.0 `Mapped[...]` typing:
| Model | Key columns |
|---|---|
| `User` | `id`, `oidc_issuer`, `oidc_subject` (unique pair), `email`, `display_name`, `roles: list[Role]`, `is_locked`, `risk_score` |
| `PrivateNote` | `id`, `owner_id → User`, `title`, `body_ciphertext: bytes`, `body_nonce`, `dek_wrapped`, `source_type`, `created_at` |
| `WikiArticle` | `id`, `title`, `status: ArticleStatus`, `supersedes_id`, `superseded_by_id`, `version`, `last_verified_at`, `created_from_promotion_id` |
| `ArticleBlock` | `id`, `article_id`, `position`, `author_id`, `consent_status: ConsentStatus`, `content`, `content_hash`, `is_honeypot` |
| `ExpertReview` | `id`, `article_id`, `reviewer_id`, `decision: ReviewDecision`, `comment`, `created_at` |
| `SecurityAuditLog` | `id: bigint identity`, `occurred_at`, `actor_id`, `event_type`, `target_type`, `target_id`, `payload: JSONB`, `prev_hash`, `hash` |

**Enums** (`db/enums.py`, `StrEnum` + PG enum types):
- `Role`: `MEMBER`, `EXPERT`, `ADMIN`
- `ConsentStatus`: `ACTIVE`, `SUSPENDED`, `REVOKED`
- `ArticleStatus`: `DRAFT`, `IN_REVIEW`, `CANONICAL`, `SUPERSEDED`, `DISPUTED` (gap G3), `REVISION_REQUIRED`, `REJECTED`
- `ReviewDecision`: `APPROVE`, `REQUEST_CHANGES`, `REJECT`, `MARK_DISPUTED`

**Alembic**
- Async env, `compare_type=True`, naming convention for constraints.
- Migration `0001_core_schema`.

**Tests**
- Integration: migrations upgrade/downgrade round-trip on a testcontainer.
- Integration: `spoldzielnia_app` cannot `CREATE TABLE`, `ALTER`, or `SET ROLE` to the owner.
- Unit: enum ↔ DB value mapping.

**Docker:** `migrate` service now runs `alembic upgrade head` with the owner DSN (from secrets).
`api` uses only the app DSN. `make smoke` additionally asserts `/api/readyz` reports the expected
Alembic head revision.

---

## Chunk 1.4 — Row-Level Security & immutable audit log

**RLS migration `0002_rls`**
```sql
ALTER TABLE private_note ENABLE ROW LEVEL SECURITY;
ALTER TABLE private_note FORCE ROW LEVEL SECURITY;

CREATE POLICY private_note_owner_isolation ON private_note
  USING      (owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
```
- With the setting unset, `NULLIF(...)` returns `NULL`, the comparison is `NULL`, so **zero rows
  come back and inserts are rejected** (fail-closed).
- The same policy pattern is applied to every private-zone table added later
  (`note_attachment`, `note_derivative` in Stage 2) (gap G9).

**RLS session helper** (`db/rls.py`)
```python
@asynccontextmanager
async def rls_session(user_id: UUID) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(user_id)}
        )
        yield s
```
- `set_config(..., is_local=true)` is the transaction-scoped equivalent of `SET LOCAL`. It's
  required because pooled connections must not leak the setting between requests.
- FastAPI dependency `get_rls_session(current_user)`.
- Repository rule: `PrivateNoteRepository` **only** accepts an RLS session (type-level: a
  `NewType("RlsSession", AsyncSession)` returned exclusively by `rls_session`).

**Immutable audit log (gap G10)** (`audit/service.py`, migration `0003_audit`)
- `REVOKE UPDATE, DELETE, TRUNCATE ON security_audit_log FROM spoldzielnia_app;`
- `BEFORE UPDATE OR DELETE` trigger raises an exception (defence in depth, applies to the owner too).
- Hash chain: `hash = sha256(prev_hash || canonical_json(row))`, computed in a
  `BEFORE INSERT` trigger with `pg_advisory_xact_lock` to serialise appends.
- `AuditService.record(event_type, actor_id, target, payload)`. `EventType` is a `StrEnum` registry.
- CLI `spoldzielnia audit-verify` (runnable as `docker compose run --rm api audit-verify`)
  re-computes the chain and reports breaks.

**Tests (mirror PLAN verification)**
- `test_rls_isolation.py` (integration):
  1. user A inserts a note via `rls_session(A)`.
  2. `rls_session(B)` → `SELECT` returns 0 rows; `UPDATE`/`DELETE` affect 0 rows.
  3. Plain session (no setting) returns 0 rows.
  4. `rls_session(B)` inserting with `owner_id=A` raises `InsufficientPrivilege` (WITH CHECK).
  5. Setting doesn't leak: reuse the pooled connection after the transaction, setting is empty.
- `test_audit_immutable.py`: UPDATE/DELETE as the app role fail; tampering with a row (as superuser
  in the test) is detected by the verifier.


**As built (decisions made during implementation)**
- `rls_session(factory, user_id)` takes the session factory explicitly. The app keeps the factory
  in `app.state`, not in a module global. The repository lives in `db/repositories.py`.
- `RlsSessionDep = Annotated[RlsSession, Depends(get_rls_session, scope="function")]`: the
  transaction commits *before* the response is sent, so a failed commit can never return a 2xx.
- `auth.deps.current_user_id` is a fail-closed placeholder (always 401) until 1.6 replaces it.
- Hash-chain trigger: after taking the advisory lock it draws a fresh `id` from the identity
  sequence, so id order equals chain order even when transactions commit in a different order than
  they started. It also forces `occurred_at = clock_timestamp()` and ignores caller-supplied
  `prev_hash`/`hash`. Appends outside `READ COMMITTED` are rejected, because an older snapshot
  could miss the latest committed row and fork the chain.
- Canonical row text = `jsonb_build_object(id, occurred_at (UTC, µs), actor_id, event_type,
  target_type, target_id, payload)::text`, rendered by Postgres built-ins
  (`audit.verify.CANONICAL_ROW_SQL`). Linking and SHA-256 are checked in Python.
- Limitation: cutting off the newest rows can be detected only against an externally recorded
  head. `audit-verify` logs `head_id`/`head_hash` for that purpose.

---

## Chunk 1.5 — OIDC Authorization Code + PKCE (Backend-for-Frontend)

**Design:** the FastAPI backend is the OAuth confidential client (BFF pattern). The browser
never sees access or refresh tokens. It only gets an opaque session cookie.

**Deps:** `authlib`, `joserfc` (or `authlib.jose`), `redis[hiredis]`.

**Components**
- `auth/oidc_client.py`: discovery (`.well-known/openid-configuration`) cached, JWKS cached
  with `kid`-miss refresh. The app trusts **one issuer: Keycloak**. Google Workspace and Entra ID
  are configured as **identity brokers inside Keycloak** (PLAN: full delegation to the IdP). This
  keeps `api` free of internet access and gives one place to enforce MFA/`acr`. The client stays
  generic, so a direct provider can still be configured if ever needed.
- `GET /auth/login?provider=keycloak&return_to=/…`:
  - generate `state`, `nonce`, `code_verifier` (43–128 chars), `code_challenge=S256`.
  - store `{verifier, nonce, return_to}` in Redis key `oidc:state:{state}` (TTL 10 min, single use).
  - `return_to` validated against a same-origin allow-list (open-redirect protection).
- `GET /auth/callback`: consume state (GETDEL), exchange code, validate ID token:
  `iss`, `aud`, `exp`/`iat` (60s leeway), `nonce`, `alg` allow-list (`RS256`, `ES256`, no `none`/`HS*`).
- **User provisioning:** upsert `User` by `(iss, sub)`. Roles are mapped from the `roles` claim via
  a configurable claim path per provider. Audit `USER_LOGIN` / `USER_PROVISIONED`.
- **No password column anywhere** (assert in a schema test).

**Tests**
- `tests/fixtures/oidc.py`: in-process RSA keypair, JWKS served via `respx`, `make_id_token(**claims)`.
- Unit: valid token → user created. Wrong `aud`/`iss`/`nonce`, expired, `alg=none`, or unknown `kid` → 401.
- Unit: replayed `state` → 400. Tampered `return_to` → falls back to `/`.
- Integration (optional, `integration`): real Keycloak login via `httpx` form-post script.

**Docker:** issuer is `https://auth.spoldzielnia.localhost/realms/spoldzielnia` for both browser and
`api` (network alias, gap G23). In dev, the OIDC `httpx` client trusts Caddy's local root CA via
`OIDC_CA_BUNDLE` (read-only mount). Prod uses public certificates and needs no custom CA.
`make smoke` checks that `GET /api/auth/login` returns a 302 to the Keycloak authorize endpoint
with `code_challenge_method=S256`.

**As built (decisions made during implementation)**
- Deps: `joserfc` (typed JOSE) + `httpx` for the code flow, `redis[hiredis]`. Authlib's Starlette
  client is untyped and assumes server-side session middleware, which this BFF doesn't use.
- Modules: `auth/oidc_client.py` (discovery + JWKS cache, exchange, ID-token validation),
  `auth/pkce.py`, `auth/login_state.py` (Redis adapter, `SET NX` + `GETDEL`), `auth/redirects.py`,
  `auth/identity.py` (claims → identity, dotted `oidc.roles_claim`), `auth/provisioning.py`,
  router `api/auth.py`.
- **Login CSRF protection (beyond the spec):** `/auth/login` also sets `__Host-oidc_login=<state>`
  (`HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=600`). The callback requires the cookie to
  equal `state`, so a callback URL planted by an attacker can't log a victim into the attacker's
  account. It's `Lax` because the IdP redirect is a cross-site top-level navigation.
- Validation details: `alg` is checked against `oidc.signing_algs` before the key lookup (settings
  refuse `none`/`HS*`). The token must have a `kid`. JWKS keys with `use=enc` are ignored. An
  unknown `kid` refetches the JWKS at most every `jwks_min_refresh_seconds`. `azp` must equal the
  client when present or when `aud` has several entries. Discovery `issuer` must match exactly,
  and the IdP must offer S256.
- Status codes: bad/unbound/replayed state, provider `error`, missing code → 400; rejected code
  or ID token → 401; IdP unreachable/misconfigured → 502; locked user → 423 (the login is still
  audited, with `outcome="locked"`).
- Provisioning: `INSERT … ON CONFLICT (oidc_issuer, oidc_subject) DO UPDATE`. Email is stored only
  when `email_verified` is true. Login never touches `is_locked`/`risk_score`. `USER_PROVISIONED`
  (first login) and `USER_LOGIN` (`provider`, `outcome`, `roles`, `acr`, `amr`; no PII) are
  written in the same transaction.
- `/auth/login` already accepts `acr_values` (only values from `security.mfa_acr_values`) and
  `prompt=login` for step-up in 1.6. The callback redirects to `return_to` without a session
  until 1.6 (`TODO(1.6)` in `api/auth.py`). The tokens are discarded until then.
- Dev CA: `caddy-ca` service (see DEPLOYMENT_DOCKER §"OIDC issuer split-horizon fix"). The OIDC
  HTTP client is created on first use, because the CA file appears only after the proxy starts.
- Integration test `test_keycloak_login.py` drives the real Keycloak forms for `member1` (pwd)
  and `expert1` (pwd + TOTP from the dev seed) and checks the stored roles and audited `amr`.

---

## Chunk 1.6 — Session cookies, refresh rotation, RBAC & MFA guards

**Sessions** (`auth/sessions.py`, Redis-backed)
- Cookie `__Host-sid`: `HttpOnly; Secure; SameSite=Strict; Path=/`, opaque 256-bit random ID.
- Redis `session:{sid}` → `{user_id, roles, acr, amr, auth_time, family_id, access_exp}`.
  Access validity **15 min**. Absolute session lifetime configurable (e.g. 12 h).
- **Refresh rotation with reuse detection:**
  - Each refresh token belongs to a `family_id`. Store `rt:{hash}` → `{family_id, used: bool}`.
  - On access expiry, the middleware refreshes with the IdP using the current RT, stores the new RT,
    marks the old one as `used`.
  - Presenting a **used** RT means a theft signal: revoke the whole family, destroy all sessions
    of that family, audit `REFRESH_TOKEN_REUSE`, return 401.
  - Concurrency: per-family Redis lock so parallel requests don't trigger false reuse.
- `POST /auth/logout`: destroy session, IdP RP-initiated logout + token revocation endpoint.

**CSRF (gap G19):** double-submit token. A `csrf` cookie (non-HttpOnly, `__Host-`) has to match the
`X-CSRF-Token` header on all unsafe methods. Also check the `Origin` header against an allow-list.

**Authorization deps** (`auth/deps.py`)
- `current_user()`: loads the session, refuses locked users (`User.is_locked`) → 423.
- `require_roles(*roles)`.
- `require_mfa()`: for `EXPERT`/`ADMIN`, require `acr ∈ settings.mfa_acr_values` **or**
  `amr ∩ {"otp","mfa","hwk","fido"} ≠ ∅`. Otherwise 403 with `WWW-Authenticate` hint →
  frontend triggers step-up (`/auth/login?acr_values=mfa&prompt=login`).
- Role-elevated endpoints get `require_roles` + `require_mfa` automatically via a router-level
  dependency.
- `GET /me`: profile + roles + `mfa_satisfied`.

**Tests (mirror PLAN verification)**
- Matrix test: `{MEMBER, EXPERT, ADMIN} × {mfa, no-mfa}` against a dummy expert endpoint and admin endpoint.
- Cookie attribute assertions on `Set-Cookie`.
- Refresh: expiry triggers rotation. Old RT reuse revokes the family and audits the event.
  Concurrent refresh produces no false positive.
- CSRF: POST without the token → 403. Cross-origin `Origin` → 403.

**Docker:** cookies work in dev because the whole app is served over HTTPS by Caddy on one origin.
The allowed `Origin` list comes from settings (`https://spoldzielnia.localhost` in dev, the real
domain in prod). Client IPs for audit and rate limiting come from `X-Forwarded-For`, trusted only
from the Caddy subnet.

**As built (decisions made during implementation)**
- Redis keys of secrets are hashed: `session:{sha256(sid)}`, `rt:{sha256(rt)}`. A Redis dump
  yields no usable cookie or token. The refresh token and the ID token (kept for
  `id_token_hint`) are sealed with AES-256-GCM, the token family id bound as associated data.
  The key is HKDF-derived from `security.session_secret` (`auth/crypto.py`).
- The access token is not stored, because the API calls no resource servers. Its expiry, capped
  at `access_token_ttl_seconds`, decides when the server re-checks with the IdP via the refresh
  grant. An IdP-side logout or disable therefore ends the session within 15 min.
- Refresh runs in the `current_session` dependency (only authenticated routes pay for it), not
  in a global middleware. Under the per-family Redis lock (`SET NX PX`, Lua compare-and-delete
  release) the family is re-read, so waiters reuse the winner's refresh (the concurrency test
  checks exactly one IdP call). A current refresh token whose `rt:` record is used, missing or
  from another family → family revoked, `REFRESH_TOKEN_REUSE` audited (with client IP), 401.
  The realm also rotates with `refreshTokenMaxReuse: 0`, so Keycloak independently rejects a
  replayed token (`invalid_grant` → session dropped).
- IdP outage during refresh → 503 + `Retry-After`, and the session is kept. IdP `invalid_grant`
  → session dropped, 401.
- CSRF: `Origin` (or `Referer`) allow-list **and** signed double submit. The token is
  `HMAC(key, sid)`, so it needs no storage and a token for another session fails. Every unsafe
  method needs a session, because no unauthenticated unsafe endpoint exists yet.
- Cookies: `__Host-sid` (HttpOnly, Strict) and `__Host-csrf` (readable, Strict) are browser-session
  cookies (no `Max-Age`). The server enforces the 12 h absolute lifetime. Because of `Strict`,
  the browser doesn't send `__Host-sid` on the very redirect that ends the cross-site login
  chain. The frontend (Stage 6) must load the session with a same-origin `fetch('/api/me')`, not
  on server-side render of that first page.
- Re-login (e.g. step-up) drops the browser's previous session. Sessions always get a fresh sid
  (no fixation).
- `current_user` reads the `app_user` row on each request: roles and `is_locked` come from the
  DB (synced at login). A locked user → 423 and the family is revoked.
- `require_mfa`: `acr ∈ security.mfa_acr_values` or `amr ∩ security.mfa_amr_values`. The realm
  has no ACR→LoA map, so in practice `amr` (`otp`) decides. 403 body
  `{"error": "mfa_required", "step_up": "/api/auth/login?prompt=login&acr_values=mfa"}` plus an
  RFC 9470 `WWW-Authenticate` challenge. `elevated_router(*roles)` adds `require_roles` +
  `require_mfa` to every route and only accepts EXPERT/ADMIN.
- `POST /auth/logout` returns `{"logout_url"}` (RP-initiated logout with `id_token_hint`) for
  the frontend to navigate to. It revokes the refresh token (RFC 7009, best effort) and audits
  `USER_LOGOUT`.
- Smoke additionally checks `/api/me` → 401 without a session and a cross-origin POST → 403.

**Stage 1 exit criteria:** chunks 1.1–1.6 green. The RLS isolation test and the OIDC role/MFA
matrix both pass in CI. On the Mac, `make up` serves the app at `https://spoldzielnia.localhost`,
and a real Keycloak login (member1) ends with a working `__Host-sid` session (`GET /api/me` → 200).
