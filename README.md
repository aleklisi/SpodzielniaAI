# Spółdzielnia AI

**[Polski](#polski) · [English](#english)**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Polski

**Spółdzielnia AI** to platforma wiedzy dla spółdzielni, stowarzyszeń i wspólnot praktyków
(np. pszczelarzy, rolników, rzemieślników). Członkowie prowadzą prywatne, zaszyfrowane notatniki.
Wybraną wiedzę mogą przekazać do wspólnej Wiki, nad którą czuwa Rada Ekspertów. Czat AI odpowiada
na pytania wyłącznie na podstawie zatwierdzonych artykułów, z cytowaniami, i jest chroniony przed
wykradaniem bazy wiedzy.

### Najważniejsze funkcje

- **Prywatny notatnik:** notatki, zdjęcia, dokumenty, nagrania i linki, szyfrowane i izolowane
  przez Row-Level Security w PostgreSQL. Nagrania są transkrybowane, dokumenty i zdjęcia opisywane
  (OCR, VLM), a każdy plik skanowany antywirusem.
- **Push-to-Wiki:** z zaznaczonych notatek model językowy przygotowuje szkic artykułu. Wcześniej
  usuwane są dane osobowe (PESEL, nazwiska, adresy; Presidio + spaCy dla języka polskiego).
- **Rada Ekspertów:** recenzja szkiców, zatwierdzanie z MFA, artykuły kanoniczne, zastąpione
  i sporne. Do czatu trafia tylko obowiązująca wersja wiedzy.
- **Pochodzenie i zgody na poziomie bloku:** każdy fragment artykułu zna swojego autora. Autor
  może w każdej chwili wstrzymać, wznowić lub wycofać swój wkład; jego fragmenty znikają wtedy
  z indeksu.
- **Czat spółdzielni (RAG):** odpowiedzi z cytowaniami artykułów, nigdy surowe fragmenty bazy.
  Gdy baza nie zna odpowiedzi, czat mówi to wprost.
- **Ochrona przed destylacją:** proof-of-work, wykrywanie anomalii semantycznych i sweepów
  bazy, kontrola dosłownych cytatów, znak wodny per konto, honeypoty i silnik ryzyka.
- **Atlas wizualny:** zdjęcia z oznaczeniami, weryfikacja ekspercka, rozpoznawanie obrazów
  i eksport zbioru danych (COCO/YOLO).
- **Dowolne modele przez API:** lokalne (LM Studio, Ollama, vLLM) lub chmurowe, wybierane per
  zastosowanie w panelu administratora. Dane prywatne trafiają tylko do dostawców dopuszczonych
  przez operatora i administratora.
- **Administracja:** użytkownicy i role (Keycloak), konfiguracja modeli z porównywarką,
  dziennik audytu z łańcuchem skrótów SHA-256, wygląd organizacji (nazwa, logo, kolory).
- **Nowoczesny interfejs:** menu po lewej, obszar roboczy po prawej, w stylu Open Notebook;
  działa na komputerze, tablecie i telefonie, zgodnie z WCAG 2.2 AA; po polsku i po angielsku.

### Technologie

Python 3.12 (FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, Celery), PostgreSQL 16 z RLS,
Qdrant, Redis, MinIO, ClamAV, Keycloak (OIDC, TOTP), LiteLLM, Docling, Presidio, Next.js
(App Router, TypeScript, Tailwind CSS, BlockNote), Caddy, Docker Compose.

### Szybki start

Wymagany Docker Desktop (zalecane co najmniej 12 GB RAM) i [uv](https://docs.astral.sh/uv/).

```bash
make up          # sekrety, budowa obrazów, start i oczekiwanie na gotowość
make trust-cert  # jednorazowo: zaufanie lokalnemu certyfikatowi Caddy
make migrate     # migracje bazy danych
```

Aplikacja działa pod `https://spoldzielnia.localhost`. Użytkownicy deweloperscy i dalsze kroki
(modele, produkcja, kopie zapasowe) są opisane w [dokumentacji technicznej](#developer-guide)
poniżej (po angielsku).

### Dokumentacja

- Specyfikacja: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), bezpieczeństwo:
  [`docs/SECURITY.md`](docs/SECURITY.md), przegląd: [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md)
- Plan i etapy: [`PLAN.md`](PLAN.md), [`docs/implementation/`](docs/implementation/00_OVERVIEW.md),
  historie użytkowników: [`docs/USER_STORIES_LOG.md`](docs/USER_STORIES_LOG.md)
- Zasady dla agentów AI: [`AGENTS.md`](AGENTS.md)

### Status

Projekt jest rozwijany. Interfejs jest dostępny po polsku i po angielsku (wybór języka w menu,
Etap 14).

### Licencja

[MIT](LICENSE) © 2026 Piotr Pacewicz

---

## English

**Spółdzielnia AI** ("AI Cooperative") is a knowledge platform for cooperatives, associations and
communities of practice (beekeepers, farmers, craftspeople and the like). Members keep private,
encrypted notebooks. They can contribute selected knowledge to a shared wiki governed by an
Expert Council. An AI chat answers questions only from approved articles, with citations, and is
protected against extraction of the knowledge base.

### Key features

- **Private notebook:** notes, photos, documents, recordings and links, encrypted and isolated
  with PostgreSQL Row-Level Security. Recordings are transcribed, documents and photos described
  (OCR, VLM), and every file is virus-scanned.
- **Push-to-Wiki:** a language model turns selected notes into a draft article after personal
  data is removed (Polish national IDs, names, addresses; Presidio + spaCy for Polish).
- **Expert Council:** draft review, MFA-protected approval, canonical, superseded and disputed
  articles. Only the current version of the knowledge reaches the chat.
- **Block-level provenance and consent:** every part of an article knows its author. Authors
  can suspend, resume or revoke their contribution at any time, and their blocks leave the index.
- **Cooperative chat (RAG):** answers cite articles and never return raw chunks of the knowledge
  base. When the knowledge base has no answer, the chat says so.
- **Anti-distillation shield:** proof-of-work, semantic anomaly and sweep detection, verbatim
  checks, a per-account watermark, honeypots and a risk engine.
- **Visual atlas:** annotated photos, expert verification, image recognition and dataset export
  (COCO/YOLO).
- **Any model over an API:** local (LM Studio, Ollama, vLLM) or cloud, chosen per purpose in the
  admin console. Private data goes only to providers cleared by the operator and an admin.
- **Administration:** users and roles (Keycloak), model configuration with a side-by-side
  playground, a SHA-256 hash-chained audit log, organisation branding (name, logo, colours).
- **Modern UI:** an Open Notebook-style layout (menu on the left, workspace on the right) for
  desktops, tablets and phones, meeting WCAG 2.2 AA; in Polish and English.

### Stack

Python 3.12 (FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, Celery), PostgreSQL 16 with RLS,
Qdrant, Redis, MinIO, ClamAV, Keycloak (OIDC, TOTP), LiteLLM, Docling, Presidio, Next.js
(App Router, TypeScript, Tailwind CSS, BlockNote), Caddy, Docker Compose.

### Quick start

Requires Docker Desktop (12 GB RAM or more recommended) and [uv](https://docs.astral.sh/uv/).

```bash
make up          # secrets, image build, start, wait until healthy
make trust-cert  # once: trust Caddy's local certificate
make migrate     # database migrations
```

The app runs at `https://spoldzielnia.localhost`. Dev users and the next steps (models,
production, backups) are in the [developer guide](#developer-guide) below.

### Documentation

- Specification: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), security:
  [`docs/SECURITY.md`](docs/SECURITY.md), review: [`docs/SECURITY_REVIEW.md`](docs/SECURITY_REVIEW.md)
  (the specification and plan are written in Polish)
- Plan and stages: [`PLAN.md`](PLAN.md), [`docs/implementation/`](docs/implementation/00_OVERVIEW.md),
  user stories: [`docs/USER_STORIES_LOG.md`](docs/USER_STORIES_LOG.md)
- Rules for AI coding agents: [`AGENTS.md`](AGENTS.md)

### Status

Under active development. The UI is available in Polish and English (choose the language in
the menu, Stage 14).

### License

[MIT](LICENSE) © 2026 Piotr Pacewicz

---

# Developer guide

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

## Production server (6.8)

One Linux server, with images from a registry. The runbook covers installing, upgrading,
rolling back, nightly encrypted backups and restoring after a loss:
[docs/ops/DISASTER_RECOVERY.md](docs/ops/DISASTER_RECOVERY.md).

```bash
cp .env.prod.example .env && $EDITOR .env   # SITE_DOMAIN, ACME_EMAIL, REGISTRY, BACKUP_*
make secrets && make deploy VERSION=v1.2.3   # pull, models, backup, migrate, up --wait, smoke
make create-admin EMAIL=admin@example.org NAME="Imię Nazwisko"
make backup                                  # age-encrypted data + secrets bundles (nightly timer)
make restore DATA=… SECRETS=… BACKUP_IDENTITY=…
make dr-rehearsal                            # dev: restore a backup of the stack into a bare copy, verify
```

Images are published by `.github/workflows/publish.yml` on a `v*.*.*` tag: multi-arch, with an
SBOM and provenance (`docker buildx bake release`).

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

To work without TOTP in development, run `make mfa-off`: Keycloak skips the OTP step and the API
treats every session as MFA-satisfied (`MFA_REQUIRED=false` in `.env`, rebuilds `api`). `make
mfa-on` restores both. Log in again after switching. The API refuses to start with MFA off when
`SPOLDZIELNIA_ENVIRONMENT=prod`. While MFA is off, the E2E step-up scenario is skipped.

### Managing users in the admin console (Stage 8)

`/admin/users` (ADMIN with MFA, nav "Użytkownicy") creates accounts, resets passwords, forces a
password change, blocks and unblocks users, ends their sessions and manages the `MEMBER`,
`EXPERT` and `ADMIN` roles. Keycloak keeps every password; the API works through a dedicated
service account, `spoldzielnia-admin`, which can manage users and nothing else in the realm.

- **Existing stacks** (created before Stage 8) need the client once: `make secrets` (adds
  `keycloak_admin_client_secret`), recreate `keycloak` and `api` (`make up`), then
  `make keycloak-setup`. Fresh dev realms get it from the realm export; run the setup anyway, it
  is idempotent.
- **New accounts** need an e-mail, first and last name (the realm's user profile requires them).
  The user sets their own password: from an e-mail link (needs SMTP in Keycloak; dev uses
  Mailpit, `--profile tools`) or a 16-character temporary password shown to the admin once.
  New experts and admins must set up TOTP at their first login (unless `make mfa-off`).
- **Guardrails:** nobody can block themselves or remove their own admin role, the last active
  admin can't be demoted or blocked, and granting EXPERT/ADMIN, creating such an account or
  unblocking needs a login with a second factor in the last 5 minutes.
- **Blocking** disables the Keycloak user and ends every session at once. It is separate from the
  security shield's lock, which is lifted in the security console after reviewing the risk
  history.
- **Users created in the Keycloak console** appear after "Synchronizuj z Keycloak"; a user
  disabled there is shown as blocked.
- Dev only: Keycloak's internal port is on `127.0.0.1:8180` for `tests/integration/test_keycloak_admin.py`.

## Private notebook & media pipeline (Stage 2)

Notes, attachments and derivatives are encrypted per row (envelope encryption, KEK = Docker
secret `kek`) and isolated by Postgres RLS. Uploads (`POST /api/notes/{id}/attachments`) are
validated by content (libmagic), checked by the archive guard, scanned by ClamAV (fail-closed),
stripped of metadata and stored encrypted in MinIO. Links (`POST /api/notes/{id}/links`) pass
SSRF checks and are fetched by `worker-egress`. Celery workers produce derivatives: transcripts,
parsed documents (Docling) and fetched pages. Recordings go to the speech-to-text model routed
for „Transkrypcja nagrań” in `/admin/llm` (any `/v1/audio/transcriptions` API: a self-hosted
whisper server, Mistral Voxtral, OpenAI…; 10-minute segments, timestamps stitched). Without one,
the built-in faster-whisper transcribes if `make models` installed it
(`LLM_BUILTIN_TRANSCRIPTION=false` turns it off); otherwise recordings wait for a model.
Documents need no model: DOCX through Docling's simple pipeline, ODT natively, PDFs from their
text layer (or Docling's layout models, if installed). Scanned pages are rendered and read by the
vision model routed for „Odczyt skanów (OCR)”, else by Docling's built-in OCR if installed
(`LLM_BUILTIN_OCR`); otherwise the scan waits for a model while text documents complete.

```bash
make models WHISPER=1 DOCLING_OCR=1 SIGLIP=1  # optional built-ins into the `models` volume
make up            # starts worker-media (no internet) and worker-egress too
make models-host   # Whisper tiny + Docling into ./.models for the `ml` tests
```

Host-side file-type tests need libmagic (`brew install libmagic`).

### Models: none required (Stage 10)

The stack starts and runs with **no model server and no downloaded models**: every model is a
provider configured in `/admin/llm` (below), local or in the cloud. A model server inside the
stack is optional: `--profile ollama` (an Ollama container) or `--profile relay` with
`LLM_RELAY_TARGET=host:port` (LM Studio or Ollama on another machine, e.g. over a VPN). In dev the
relay to the server on your Mac is always on, as `http://llm:11434`. Built-in engines (Whisper,
Docling OCR, SigLIP) are opt-in (`make models WHISPER=1 …` + `LLM_BUILTIN_*=true`; dev: on). The
Members see which providers process their private data on „Twoje dane” (`/privacy`), and a
newly cleared cloud provider shows them a one-time banner. The API-only profile and its
measured memory are in
[DEPLOYMENT_DOCKER.md §6.1](docs/implementation/DEPLOYMENT_DOCKER.md). `.env` can also name the
dev models:

```bash
LLM_HOST_PORT=1234                                # LM Studio (Ollama: 11434)
LLM_CHAT_MODEL=speakleash/bielik-11b-v2.3-instruct # as listed by GET /v1/models
LLM_VLM_MODEL=qwen2.5-vl-7b-instruct               # optional: image descriptions
LLM_EMBED_MODEL=text-embedding-embeddinggemma      # Stage 4
LLM_STRUCTURED_OUTPUT=prompt                      # if the server 400s on JSON-schema output (LM Studio + Bielik)
```

If the server requires a token (LM Studio), paste it into `secrets/llm_api_key`. Private data
goes only to providers cleared for it (below): without one, promotion and image descriptions
fail with `ModelUnavailable` / `vision_unavailable`, and nothing falls back to another model.

### Choosing models in the admin console (Stage 7)

`/admin/llm` (ADMIN with MFA) manages providers, models and which model serves each purpose.
On the first visit the `.env` models above are imported as revision 1; from then on the
**active revision in the database is authoritative** and every process picks up a change
without a restart. Changes go into a draft, models that take over a purpose must pass a probe,
and any earlier revision can be restored.

`.env` stays the ceiling: admins can only choose from what it allows.

```bash
LLM_LOCAL_ENDPOINTS=["http://vllm:8000"]  # extra local OpenAI-compatible servers (origins)
LLM_ALLOW_CLOUD=false                     # true: the cooperative chat (RAG) may use a cloud model
LLM_CLOUD_HOSTS=["api.openai.com"]        # https hosts admins may configure
# LLM_PRIVATE_DATA_ENDPOINTS=["http://llm:11434","https://api.mistral.ai"]  # see below
# SPOLDZIELNIA_LLM__CONFIG_SOURCE=env     # emergency: ignore the database and use .env only
```

Private-data purposes (notes, promotion, PII scrubbing, images, embeddings, speech-to-text) can only use
providers **cleared for private data** (Stage 10.1, AGENTS.md rule 4). An endpoint can be cleared
only if it is on `LLM_PRIVATE_DATA_ENDPOINTS` (unset: the local endpoints; `[]`: none), and an
admin clears it in „Dostawcy” → „Dopuść do danych prywatnych” with a fresh login, recording the
data region, retention and contract. A cloud API (e.g. Mistral in the EU) can be cleared this
way; un-clearing stops its private purposes at once. This is enforced by the UI, the API,
database triggers and the gateway. Cloud calls go
out through the `llm-egress` proxy (CONNECT to `LLM_CLOUD_HOSTS` only); the api and worker-media
have no other route to the internet. Changing the embedding model in the console builds a new version of the
search index in the background (each version records its model and dimension, probed from the
model); the old one keeps answering, with its own model, until the new one is complete
(`GET /admin/llm/index`; `make reindex` does the same on demand).

## Test markers

`integration` (Docker services), `ml` (heavy models), `e2e` (running stack). Run a subset with
`uv run pytest -m "not integration"`.

### The visual atlas (Stage 9)

Members annotate photos in their notebook („Oznacz”: a domain, a caption, rectangles with a
label from the expert vocabulary and a description), send them to the shared **Atlas**, and
two experts (`governance.review_quorum`) verify them. „Rozpoznaj” on a notebook photo suggests
marked elements from verified atlas photos, with models cleared for private data only.

- **Domains and labels:** admins create domains in `/admin/atlas` („Dziedziny atlasu”); experts
  curate each domain's labels in `/review/labels`; members can propose labels. For a demo,
  `docker compose exec api spoldzielnia atlas seed-example` loads an example beekeeping domain
  (refused in prod).
- **Sending to the atlas** makes a copy without metadata (no GPS), removes personal data from
  the texts, screens them for prompt injection and checks the photo for people with the local
  vision model. The member sees exactly what will be shared, chooses whether their name is
  shown, and separately whether the photo may be exported. Experts review it in `/review/atlas`.
- **Consent:** authors suspend, resume or revoke photos and change both choices any time in
  „Moje kontrybucje”.
- **Recognition** needs an image embedding model: the one routed for „Atlas: wektory zdjęć” in
  `/admin/llm` (an OpenAI-style `/v1/embeddings` server for CLIP/SigLIP, e.g. Infinity), or the
  built-in SigLIP (`make models` downloads it, ~800 MB; `LLM_BUILTIN_IMAGE_EMBED=false` turns it
  off). Without either, „Rozpoznaj” is off and approved photos wait for indexing. Changing the
  image model builds a new version of the atlas index in the background and swaps it in
  (`GET /admin/llm/index`). Calibrate `ATLAS_RECOGNITION_MIN_SCORE` (`.env`)
  with `docker compose exec worker-core spoldzielnia atlas benchmark` once the atlas holds real
  photos.
- **Dataset export** (COCO/YOLO) is off until the operator sets
  `ATLAS_EXPORT_ENABLED=true` in `.env`; then admins export from `/admin/atlas` with a fresh
  MFA login; each download link works once.

