# Stage 7 — Admin-Configurable LLM Models (local vs. cloud, playground, revisions)

**User story:** [US-01](../USER_STORIES_LOG.md#us-01) — *As an administrator I want to configure LLM
models in the UI, so that I can decide whether to use locally hosted or cloud models and test
different models with an easy way to change the configuration.*
**Spec refs:** `AGENTS.md` §3.3 (anti-distillation), §3.4 (local LLM for sensitive data) ·
`docs/ARCHITECTURE.md` (local models for private data; cloud optional per organisation policy) ·
chunk 3.4 (`llm/gateway.py`) · chunk 6.6 (admin console) · gap G22.

> This stage is **outside the original 6-stage plan**. It starts only after 6.7/6.8 are closed
> or on an explicit instruction. The Definition of Done from `00_OVERVIEW.md` §2.1 applies to every
> chunk (ruff, pyright strict, pytest; npm lint/typecheck/test for frontend chunks).

---

## 0. Current state (what we change)

- `LlmSettings` (`core/settings.py`) is read once from env/Docker secrets. Three local deployments
  (`local-chat`, `local-vlm`, `local-embed`) share one `local_base_url` and one key; an optional
  `cloud-chat` exists behind `allow_cloud`.
- `LiteLLMGateway` is built from settings in `main.py`, `workers/runtime.py`, `vector/reindex.py`
  and `shield/tools.py`. Changing a model = edit `.env` + restart api and workers.
- `route()` maps purpose → deployment by kind only (chat/vlm/embed). All chat purposes share one
  model; `RAG` is the only purpose that may reach the cloud.
- The `api` container sits on `edge` + `backend` (both `internal: true`): **it has no route to the
  internet today**, so a cloud deployment could not actually be reached in prod.

## 1. Goals and non-goals

**Goals**
1. Admin manages **providers** (endpoints) and **models** in the UI: local (Ollama, LM Studio,
   vLLM) and cloud (OpenAI-compatible APIs and other LiteLLM providers on an allowlist).
2. Admin assigns a model **per purpose** (`RAG`, `PROMOTION`, `PRIVATE_NOTE`, `PII_SCRUB`, `VLM`,
   `EMBED`) instead of one shared chat model.
3. Changes are **versioned revisions**: draft → test → activate, one-click rollback, full audit.
4. A **playground** to test a model before switching: connection check, model discovery,
   structured-output probe, latency/tokens, side-by-side comparison on a synthetic prompt set.
5. Changes take effect **without restarting** api and workers.

**Non-goals (v1)**
- Live switching of the **embedding model** (vector dimension and the whole index change). Shown
  read-only in v1; a guarded blue/green re-embedding flow is a follow-up story (US-02 candidate).
- Per-user or per-tenant model choice; cost dashboards / billing.
- Fine-tuning or model downloads from the UI (`ollama pull` stays an operator task).

## 2. Security invariants (must hold after this stage)

| # | Invariant | Enforcement (defence in depth) |
|---|-----------|--------------------------------|
| S1 | `ALWAYS_LOCAL` purposes (PII scrubbing, private notes, promotion, VLM, embeddings) and every `force_local_llm=True` call never reach a cloud provider. | Service validation on save · DB trigger rejecting a CLOUD provider on those purposes · `gateway.route()` still re-checks at call time and raises `LocalModelUnavailable` (no fallback, ever). |
| S2 | The UI can only **narrow** what the operator allows. | Env stays the ceiling: `llm.allow_cloud`, `llm.cloud_host_allowlist`, `llm.local_endpoint_allowlist`. With `allow_cloud=false` cloud providers can be stored but not activated, and the gateway ignores them. |
| S3 | An admin-entered URL is not an SSRF primitive into the backend network. | LOCAL base URLs must match `llm.local_endpoint_allowlist` (e.g. `http://llm:11434/v1`, host relay); CLOUD URLs must be `https://` on `llm.cloud_host_allowlist` and go out only through the egress proxy (7.3). Redirects are not followed. |
| S4 | A host cannot be declared "local" to dodge S1. | Locality is derived from the allowlist the URL matched, not from a checkbox. |
| S5 | Provider API keys are secrets. | Envelope-encrypted with the KEK (`core/crypto.py`, AAD bound to the row), write-only in the API (responses show `key_set: true` and the last 4 chars), never logged, never in audit details. |
| S6 | Anti-distillation (§3.3) is unchanged by the model choice. | Every path to a model still goes through `AntiDistillationGuard` / `OutputVerifier` / `Watermarker`. The playground never performs retrieval and never sees canonical or private content. |
| S7 | Every change is attributable. | `LLM_CONFIG_*` events in the hash-chained `SecurityAuditLog` (actor, revision, diff of non-secret fields). ADMIN + MFA on all routes (`elevated_router`). |

## 3. Design

### 3.1 Data model (migration `0012_llm_config`)

```
llm_provider        id, name (unique), kind LOCAL|CLOUD (derived, S4), base_url,
                    litellm_provider ('openai' default; allowlisted others),
                    api_key_wrapped_dek, api_key_blob (nullable, S5),
                    structured_output json_schema|prompt|auto, timeout_seconds,
                    created_by, created_at, updated_at, disabled_at
llm_model           id, provider_id → llm_provider, model_name, capability CHAT|VLM|EMBED,
                    temperature, max_tokens, embed_dimension (EMBED only), notes,
                    last_test_at, last_test_status, last_test_report (jsonb, no prompts/output)
llm_config_revision id, number (monotonic), status DRAFT|ACTIVE|ARCHIVED, comment,
                    created_by, created_at, activated_by, activated_at,
                    snapshot jsonb   -- frozen copy of providers+models+routing (no secrets)
llm_routing         revision_id → llm_config_revision, purpose (LLMPurpose), model_id → llm_model
                    PK (revision_id, purpose)
```

- Exactly one `ACTIVE` revision (partial unique index). ACTIVE/ARCHIVED revisions are immutable
  (trigger, same pattern as the audit log).
- Trigger on `llm_routing`: purposes in `ALWAYS_LOCAL` may only point at a model whose provider
  is `LOCAL` (S1, second layer); capability must fit the purpose (`VLM`→VLM, `EMBED`→EMBED, the
  rest → CHAT).
- Tables are global configuration (not user data): no RLS policy per user; the app role gets
  `SELECT` on all, `INSERT/UPDATE` only on what the admin service writes. Workers read only.
- **Seeding:** if no revision exists, `bootstrap` creates revision 1 from the current
  `LlmSettings` (env), so existing deployments behave exactly as today.

### 3.2 Runtime resolution and hot reload

- `llm/config.py`: `LlmConfigStore` loads the ACTIVE revision (+ decrypted keys) into an immutable
  `ResolvedLlmConfig`. Cached in-process; invalidated by a Redis pub/sub message
  `llm:config:activated` and by a cheap revision-number check (TTL 30 s) as a safety net.
- `LiteLLMGateway` takes a config source instead of `LlmSettings`: deployments become one per
  (purpose → model) pair, named `<purpose>@r<revision>`. When the revision changes, a new Router is
  built and swapped atomically; in-flight calls finish on the old one.
- `route()` keeps the same signature and **still enforces S1/S2 in code** (a tampered DB row
  must not be enough to send private data to the cloud).
- Env-only mode stays available (`llm.config_source = env|db`, default `db` once seeded) for
  tests, CLI jobs and emergency override.
- Workers (`worker-core`, media workers) use the same store; they have DB and Redis access
  already. `vector/reindex.py` and `shield/tools.py` switch to the store.
- EMBED in v1: the active embed model and dimension are displayed; activation of a revision
  that changes the embed model or `embed_dimension` is refused (`409 embed_change_requires_reindex`).

### 3.3 Cloud egress

- New compose service `llm-egress`: a forward proxy (smokescreen or squid) on `backend` + `egress`,
  CONNECT only to `llm.cloud_host_allowlist`, deny private ranges (reuse the egress firewall rules
  from 2.4). Only cloud deployments get `HTTPS_PROXY`-equivalent config (per-deployment httpx
  client); local calls never go through it.
- `api` stays without direct internet. Workers never get the proxy (all their purposes are local).

### 3.4 Admin API (`api/admin_llm.py`, prefix `/admin/llm`, ADMIN + MFA)

| Method & path | Purpose |
|---|---|
| `GET /providers`, `POST /providers`, `PATCH /providers/{id}`, `DELETE /providers/{id}` | CRUD (delete = disable if referenced by any revision). Key is write-only. |
| `POST /providers/{id}/test` | Reachability + auth (`GET /v1/models`), latency; returns discovered model ids. |
| `GET /models`, `POST /models`, `PATCH /models/{id}` | Models per provider (from discovery or typed in). |
| `POST /models/{id}/probe` | Synthetic probe: plain completion, JSON-schema output (detects whether `json_schema` works or `prompt` mode is needed), Polish sanity prompt, VLM test image / embed dimension check. Stores a report (timings, token counts, pass/fail — no prompt or output text). |
| `GET /revisions`, `GET /revisions/{n}` | History with diffs. |
| `POST /revisions` | Create DRAFT (copy of ACTIVE or of a given revision). |
| `PUT /revisions/{n}/routing` | Edit purpose → model in a DRAFT; validation errors name the violated invariant. |
| `POST /revisions/{n}/activate` | Requires every assigned model to have a passing probe ≤ 24 h old; publishes the reload message. |
| `POST /revisions/{n}/rollback` | Re-activates an ARCHIVED revision (creates a new revision number, keeps history linear). |
| `POST /playground` | Free-form or preset prompt against 1–3 models side by side. **No retrieval**, no access to notes/wiki; rate-limited (e.g. 30/h per admin); prompt hashed in the audit log, not stored. Cloud models are allowed here only if `allow_cloud` and only with the admin's typed text. |
| `GET /status` | Active revision, per-deployment health and recent error rate. |

Audit events (`audit/events.py`): `LLM_PROVIDER_CHANGED`, `LLM_MODEL_CHANGED`,
`LLM_CONFIG_ACTIVATED`, `LLM_CONFIG_ROLLED_BACK`, `LLM_PLAYGROUND_USED`, `LLM_ROUTE_REFUSED`
(gateway refused a cloud route for a local-only call — should never happen; alert in 6.6 console).

Metrics (`core/metrics.py`): `llm_request_seconds{deployment,purpose}`,
`llm_request_errors_total{deployment,kind}`, `llm_config_revision` gauge.

### 3.5 Frontend (`/admin/llm`, nav item "Modele AI", role ADMIN)

- **Dostawcy (Providers):** table with kind badge (Lokalny / Chmura — derived, not editable),
  status dot from the last test, "Testuj połączenie", add/edit dialog (key field write-only,
  "zmień klucz" instead of showing it). Cloud form disabled with an explanation when
  `allow_cloud=false`.
- **Modele:** per provider, "Wykryj modele" (discovery), probe button with the report
  (structured output: ✓ json_schema / ⚠ only prompt mode / ✗).
- **Przypisania (Routing):** purpose × model matrix of the DRAFT revision. Local-only purposes
  show a lock and only list local models. Diff against ACTIVE, "Aktywuj" with a confirm dialog
  summarising what changes (and a warning badge for every purpose that goes to the cloud).
- **Playground:** prompt box + preset picker, up to 3 models side by side: answer, latency,
  tokens, JSON validity. Notice that no knowledge-base content is used.
- **Historia:** revisions list, diff view, rollback.
- Same conventions as 6.6: typed client from `openapi.json`, MSW tests, Tailwind tokens, light/dark.

## 4. Chunks

### Chunk 7.1 — Domain model, migration, encrypted secrets, seeding
- Migration `0012_llm_config` (tables, partial unique index, immutability + locality triggers,
  grants). SQLAlchemy models in `db/models/llm.py`. Settings: `config_source`,
  `local_endpoint_allowlist`, `cloud_host_allowlist`, `cloud_providers_allowlist`.
- `llm/config_service.py`: create/update providers & models, URL classification (S3/S4), key
  encryption (S5), revision lifecycle, validation of invariants S1/S2.
- `bootstrap`: seed revision 1 from env.
- **Tests:** unit — URL classification table (private IPs, `localhost`, DNS names, `http` cloud
  URLs rejected, allowlist matches); integration — trigger rejects a CLOUD model on `PII_SCRUB`
  even with direct SQL; ACTIVE revision immutable; key round-trip and AAD binding (blob copied to
  another provider row fails); seeding reproduces current env routing.

### Chunk 7.2 — Config store & hot-reloading gateway
- `LlmConfigStore` (cache + Redis pub/sub + TTL check), per-purpose deployments, atomic Router swap.
- Wire api, workers, `reindex`, `shield/tools` to the store; env mode kept.
- **Tests:** unit — `route()` refuses cloud for every `ALWAYS_LOCAL` purpose and for
  `force_local_llm=True` even when the resolved config says otherwise (tampered config fixture);
  `allow_cloud=false` hides cloud deployments; revision switch mid-flight (old call completes on old
  router, next call uses new); integration — activating a revision changes the model used by a
  running worker without restart.

### Chunk 7.3 — Cloud egress proxy
- `llm-egress` service in `compose.yaml`, allowlist from `.env`, per-deployment proxy for CLOUD only.
- **Tests:** compose smoke — api can reach an allowlisted host via the proxy, cannot reach a
  non-allowlisted host or a private range; local deployments do not use the proxy; workers have no
  proxy route.

### Chunk 7.4 — Admin API, probes, audit & metrics
- Router `api/admin_llm.py`, probe runner with synthetic fixtures (`llm/probes.py`), audit
  events, metrics.
- **Tests:** authz matrix (MEMBER, EXPERT, ADMIN without MFA → 403); key never present in any
  response or audit row; activation blocked without fresh passing probes; embed change → 409;
  rollback creates a new revision; probe correctly reports `json_schema` unsupported (fake server
  returning 400 → recommends `prompt` mode, mirroring the LM Studio + Bielik case from 3.4).

### Chunk 7.5 — Playground & model comparison
- `POST /admin/llm/playground` and preset prompt set (Polish Q&A, JSON extraction, summarisation of
  a synthetic beekeeping note with fake PII → local only).
- **Tests:** no retrieval dependency is injected (structural test), rate limit, audit stores only
  a hash, cloud model refused when `allow_cloud=false`, presets containing synthetic PII refused on
  cloud models.

### Chunk 7.6 — Frontend `/admin/llm`
- Pages/components per §3.5, `src/lib/llm-admin.ts`, nav entry, regenerated `openapi.json`.
- **Tests:** Vitest + MSW — lock on local-only purposes, cloud form disabled when not allowed,
  activation confirm dialog lists cloud-bound purposes, key field never pre-filled, probe report
  states.

### Chunk 7.7 — E2E, security review, docs
- Playwright `9-llm-config.spec.ts`: admin adds a second local model, probes it, assigns it to
  `RAG` in a draft, activates, asks a chat question (answer reports the new deployment in
  diagnostics), rolls back. Member gets 403 on `/admin/llm`.
- Update `docs/SECURITY_REVIEW.md` (LLM02 / LLM10 / API7 SSRF entries for admin-entered URLs),
  `README.md` (`.env` becomes the ceiling + seed), `00_OVERVIEW.md` checklist.

## 5. Risks and open questions

| # | Topic | Proposal |
|---|-------|----------|
| Q1 | Which cloud providers in v1? | OpenAI-compatible APIs only (`openai/` in LiteLLM, as today); others (Anthropic, Azure, Bedrock) via `cloud_providers_allowlist` later. |
| Q2 | Should `RAG` on the cloud be allowed at all? Canonical wiki blocks are authored content under consent; sending them to a third party may conflict with creator consent terms. | Default **off** even with `allow_cloud=true`: a separate `llm.allow_cloud_rag` flag + a governance note to confirm with the cooperative board. |
| Q3 | Embedding model change | Follow-up story: blue/green collections + `reindex` job + switch, with a progress view. |
| Q4 | Workers caching a stale config if Redis is down | TTL revision check (30 s) caps staleness; a failed load keeps the last good config and raises an alert. |
| Q5 | Probes on large local models are slow | Probes run as a Celery task with progress polling, timeout per probe. |

---

## 6. Implementation notes (as built)

### 7.1
- Tables, enums and triggers in `0012_llm_config`; ORM in `db/models/llm.py`; service in
  `llm/config_service.py`; allowlist classification in `llm/endpoints.py`; the frozen snapshot
  schema in `llm/snapshot.py` (deployment keys are model ids, which the revision trigger reads).
- Revision numbers are an identity column. Draft routes live in `llm_routing`; activation freezes
  them into `snapshot` and the runtime reads only the snapshot, so editing a provider or model
  changes nothing until the next activation. Exception: API keys are read from the provider row,
  so a key rotation applies at once.
- **Seeding** happens on the first admin request (`ensure_seeded`), not in the `bootstrap` job:
  bootstrap has no database credentials. Until then, every process keeps using `.env`
  (identical behaviour). Imported providers use `inherit_env_key` (the Docker secret), so the key
  is not copied into the database.
- Activation checks policy (S1–S4) for all routes before probe freshness, so the admin sees the
  more useful error first. Rollback does not require probes but re-checks the current policy.
- `llm.allow_cloud` stays the single ceiling flag. Today RAG is the only purpose that is allowed
  to use the cloud, so a separate `allow_cloud_rag` flag (Q2) would duplicate it. Before enabling
  it in production, confirm with the cooperative's board that creators' consent covers sending
  canonical content to a third party.
- Settings: `llm.config_source`, `local_endpoints`, `cloud_hosts`, `egress_proxy_url`,
  `config_ttl_seconds`, `probe_max_age_hours`, `playground_per_hour`.

### 7.2
- `llm/config_store.py` (`LlmConfigStore`, a `ConfigSource`) and `ResolvedLlmConfig` /
  `env_config` / `StaticConfig` in `llm/gateway.py`. The gateway routes **per purpose**; each
  deployment carries its own temperature, max tokens, structured-output mode and timeout.
- Reload signal: activation and rollback `SET llm:config:revision <n>`; a process reloads when
  that number is newer than its own, and in any case after `llm.config_ttl_seconds`. That is one
  Redis GET per model call, instead of a pub/sub subscriber task in every process.
- LiteLLM's Router was replaced by `LiteLLMTransport` (direct `litellm.acompletion` /
  `aembedding`, `max_retries=1`, no fallbacks): the Router builds its own HTTP clients, and cloud
  deployments need one that goes through the egress proxy. One transport per revision; calls in
  flight finish on the old one.
- The gateway refuses a non-local route for a local-only call even if the configuration says so,
  and counts it in `llm_route_refused_total` (a log line, not an audit event: the gateway has no
  database session). Metrics: `llm_request_seconds`, `llm_request_errors_total`,
  `llm_config_revision`.
- The vision engine runs each call on its own event loop (worker thread). It gets a
  `StaticConfig` of the store's last configuration, refreshed on the worker's loop before each
  derivative job.
