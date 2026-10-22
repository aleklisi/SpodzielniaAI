# Stage 10 — Every model over a configurable API (no built-in LLM or embedding model required)

**User story:** [US-04](../USER_STORIES_LOG.md#us-04) — *Spółdzielnia AI should not need a local
LLM or embedding model. Everything should be configurable and provided over an API: a model run
locally (e.g. in LM Studio) or in the cloud.*

**Spec refs:** `AGENTS.md` §3 rule 4 (changed by this stage, see §2) · `docs/ARCHITECTURE.md`
§1 (local models), §4 (ingestion pipeline) · `docs/SECURITY.md` (LLM02, data protection) ·
chunks 2.5 (media workers), 3.2 (PII), 3.4 (LLM gateway), 4.1/4.4 (Qdrant, outbox),
7.1–7.7 (admin LLM configuration), 9.8–9.9 (atlas image embeddings, recognition).

> This stage is **outside the original 6-stage plan**. It starts only on an explicit instruction.
> The Definition of Done from `00_OVERVIEW.md` §2.1 applies to every chunk (ruff, pyright strict,
> pytest; npm lint/typecheck/test for frontend chunks).

**Refinement decisions (product owner, 2026-10-22):**
- **Private data goes to providers an admin has cleared for it.** Locality no longer decides.
  Each provider has a "cleared for private data" flag. Example: LM Studio on the cooperative's
  machine, or Mistral (EU) under a data processing agreement. Everything else only ever gets the
  public wiki. AGENTS.md rule 4 changes from "local" to "cleared" (§2).
- **Image embeddings are configurable.** A new purpose takes either a multimodal embedding API
  (cloud or self-hosted) or the built-in SigLIP, which becomes an optional extra. With neither,
  recognition is off.
- **Speech-to-text (Whisper) and document OCR (Docling's models) move to APIs too.** The
  prompt-injection classifier stays built in.
- **A missing model turns the feature off with a clear message.** The stack always starts. Work
  that needs the model waits and resumes once an admin configures one. There is never a
  fallback to another provider.

UI names: „Dostawcy modeli” (providers), „Dopuszczony do danych prywatnych” (the flag),
„Model nie jest skonfigurowany” (feature off), „Gdzie przetwarzane są Twoje dane” (the
members' transparency page).

---

## 0. Current state (what we build on)

- **LLMs are already reached over the OpenAI-compatible API.** The gateway (3.4, 7.2) calls
  chat, vision and text-embedding models through LiteLLM. In dev the `llm` service is a socat
  relay to LM Studio on the host; in prod it is an **Ollama container** (`compose.yaml`).
- **Admins configure models in `/admin/llm` (Stage 7):** providers, models, probes, revisions
  and routing per purpose (`LlmRoutePurpose`: PII_SCRUB, PRIVATE_NOTE, PROMOTION, VLM, EMBED,
  RAG). Capabilities: CHAT, VLM, EMBED.
- **Rule 4 today is enforced as locality.** `LlmProviderKind` (LOCAL/CLOUD) is derived from the
  endpoint allowlists (`llm.local_endpoints` vs `llm.cloud_hosts`, S3/S4). `ALWAYS_LOCAL` =
  {PII_SCRUB, PRIVATE_NOTE, PROMOTION, VLM, EMBED} is enforced in the config service, by the
  database (migration 0012 trigger) and by the gateway; callers pass `force_local_llm=True`.
- **Cloud calls leave only through `llm-egress`** (7.3, CONNECT proxy, host allowlist). Only the
  `api` is on the `llm-cloud` network; the workers are on `backend` only.
- **Models built into the workers** (downloaded by `models-init` into the `models` volume):
  faster-whisper (worker-media), Docling layout/OCR models (worker-media), SigLIP image
  embeddings (worker-core, 9.8), the LLM-Guard injection classifier (worker-core), plus spaCy
  `pl_core_news_lg` for Presidio.
- **The text-embedding dimension is fixed by env** (`LLM_EMBED_DIM` → Qdrant collection size);
  changing the embedding model means a manual `reindex`.
- **Measured memory (dev stack, idle, 2026-10-22):** ~4.8 GB without any model server; workers
  peak at 4 GB (media) + 3 GB (core) while transcribing/parsing.

## 1. Goals and non-goals

**Goals**
1. The stack starts and is useful **with no model running on the server**: no Ollama container,
   no GPU, no model downloads required.
2. Every model the app uses is a **provider + model chosen in `/admin/llm`**: chat, vision, text
   embeddings, image embeddings, speech-to-text, OCR. A provider is an OpenAI-compatible API
   (LM Studio, Ollama, vLLM, llama.cpp server, a self-hosted embedding server) or a cloud API
   (Mistral, OpenAI, others on the operator's allowlist).
3. Private data reaches **only providers cleared for it**, enforced in the same four places as
   today (env ceiling, config service, database, gateway).
4. Switching the embedding model is safe and routine: the new index is built next to the old
   one and swapped in when complete.
5. Members can see where their data is processed.

**Non-goals**
- Removing Presidio/spaCy, the LLM-Guard classifier or ClamAV: they are not LLMs, run on CPU and
  are part of the defence.
- Provider-specific features beyond what the purposes need (fine-tuning, batch APIs, tools).
- Automatic fallback between providers (excluded by the refinement decision).
- Production deployment scripts (still 6.8).

## 2. Security invariants (must hold after this stage)

**New text of AGENTS.md rule 4** (replaces "Lokalny LLM dla danych wrażliwych"):

> **4. Dane wrażliwe tylko u dopuszczonych dostawców:** Anonimizacja (PII Scrubbing), prywatne
> notatki, szkice promocji, obrazy i embeddingi danych prywatnych trafiają wyłącznie do
> dostawców modeli dopuszczonych do danych prywatnych (lista operatora w `.env` + zgoda ADMIN
> z MFA). Wymuszaj flagę `private_data=True`; brak dopuszczonego dostawcy wyłącza funkcję —
> nigdy nie przełączaj na innego dostawcę.

| # | Invariant | Enforcement |
|---|-----------|-------------|
| P1 | Private purposes use cleared providers only. | `PRIVATE_PURPOSES` = PII_SCRUB, PRIVATE_NOTE, PROMOTION, VLM, EMBED, IMAGE_EMBED, TRANSCRIBE, OCR. The gateway refuses a route to an uncleared provider whatever the caller or the database says (`llm_route_refused_total`); a database trigger refuses such routing rows; the config service returns 409 `provider_not_cleared`. `force_local_llm` is renamed `private_data` (same semantics: forces P1 for any purpose). |
| P2 | The operator sets the ceiling, admins narrow it. | A provider can be cleared only if its origin is on `llm.private_data_endpoints` (env). An empty list means nothing can be cleared and private features stay off. Same pattern as S2: the UI can narrow, never widen. |
| P3 | Clearing a provider is deliberate and recorded. | ADMIN + MFA + step-up (≤ 5 min). Required fields: data region, retention ("brak przechowywania" / days), contract reference (DPA). Audit `LLM_PROVIDER_CLEARED` / `…_UNCLEARED` with those fields. Un-clearing takes effect at once (reload signal) and is not blocked by routes: the affected purposes go to "not configured". |
| P4 | Members know where their data goes. | „Gdzie przetwarzane są Twoje dane” lists the cleared providers with region, retention and since-when. Clearing a new cloud provider shows a one-time banner to every member. |
| P5 | No silent fallback. | A purpose without a usable model raises `ModelNotConfigured`. Jobs park as `WAITING_FOR_MODEL` and resume when a revision is activated. Interactive features answer 503 `model_not_configured`. No provider is ever substituted. |
| P6 | Endpoints are still not an SSRF primitive. | S3/S4 unchanged: every base URL must match an allowlist; https cloud hosts go through `llm-egress`; workers reach the internet only through that proxy (they join `llm-cloud`, which routes nowhere else). Smoke test: a worker cannot open a direct connection out. |
| P7 | Keys stay secret. | Unchanged S5: envelope-encrypted, write-only, never logged. New: keys of uncleared providers are never loaded by workers that only run private purposes. |
| P8 | Re-embedding never mixes vector spaces. | Every collection records the model and dimension that built it. A query is embedded with the model recorded on the collection it searches. A model change builds a new collection version and swaps an alias when it is complete (§3.4). |
| P9 | Audio and documents leave only for cleared providers. | TRANSCRIBE and OCR are private purposes (P1). Bytes are sent from the worker's decrypted temp file, never from a public URL. The provider's text output goes through the injection scan as today. |

## 3. Design

### 3.1 Trust model: locality → clearance

`llm_provider` gains `private_data_cleared bool`, `cleared_region`, `cleared_retention`,
`cleared_contract_ref`, `cleared_by`, `cleared_at`. `kind` (LOCAL/CLOUD) stays, derived from
the allowlists, but it now only decides **the network path** (direct vs `llm-egress`), not
what data may be sent.

- **Migration:** existing LOCAL providers on `llm.private_data_endpoints` are cleared
  automatically, so behaviour is unchanged after upgrade. The 0012 locality trigger is replaced by
  a clearance trigger, and the `LOCAL_ONLY` tuple in that frozen migration is not touched.
- **Default `llm.private_data_endpoints`:** the local endpoints (`llm.local_base_url` +
  `llm.local_endpoints`). A cloud host becomes clearable only when the operator adds it, e.g.
  `LLM_PRIVATE_DATA_ENDPOINTS=["http://llm:11434","https://api.mistral.ai"]`.
- **Gateway:** `ALWAYS_LOCAL` → `PRIVATE_PURPOSES`; the check reads the snapshot's
  `cleared` flag, re-validated against env on load (a tampered row cannot clear a provider).

### 3.2 Purposes and capabilities

| Purpose | Capability | Private | Notes |
|---|---|---|---|
| RAG | CHAT | no | Canonical wiki only (Stage 5). |
| PII_SCRUB, PRIVATE_NOTE, PROMOTION | CHAT | yes | Unchanged. |
| VLM | VLM | yes | Image descriptions, atlas privacy check, recognition. |
| EMBED | EMBED | yes | One text-embedding model for all collections (private notes and wiki share the vector space). |
| **IMAGE_EMBED** (new) | IMAGE_EMBED | yes | Atlas regions and recognition crops. |
| **TRANSCRIBE** (new) | TRANSCRIBE | yes | `/v1/audio/transcriptions`. |
| **OCR** (new) | VLM (default) or OCR | yes | Scanned pages → text (§3.6). |

New capabilities get probes: TRANSCRIBE (a synthetic Polish clip, word error rate below a
threshold), IMAGE_EMBED (two crops of one synthetic object are closer than a different object,
and the probe records the dimension), OCR (a rendered Polish paragraph read back).

### 3.3 "Not configured" behaviour

- `GET /api/capabilities` (members) returns which features are available: `notebook_ai`,
  `transcription`, `ocr`, `image_descriptions`, `wiki_answers`, `promotion`, `recognition`.
  The frontend hides or disables each one with „Model nie jest skonfigurowany” and, for
  admins, a link to `/admin/llm`.
- Uploads are always accepted and stored. Their derivatives wait in `WAITING_FOR_MODEL`, and
  activating a revision re-queues the waiting jobs for the purposes it now serves.
- Startup no longer needs `LLM_EMBED_DIM` or a reachable model; bootstrap creates collections
  lazily, when the first embedding model is activated (§3.4).
- `/admin/llm` shows a „Brakujące modele” checklist; health stays green, and a metric reports
  the purposes that have no model.

### 3.4 Embedding model changes (text and image)

- Collections become versioned: `<name>__<rev>` behind a Qdrant alias `<name>`. The payload
  schema is unchanged.
- Activating a revision that changes EMBED or IMAGE_EMBED (a different model or dimension)
  creates the new collection versions and queues a **re-embed job** through the outbox. The
  old versions keep serving until the new ones are complete; then the alias swaps and the old
  versions are dropped after a grace period.
- Consent changes made during re-embedding are applied to both versions (outbox fan-out), and
  the consistency audits (4.4, 9.8) check both. P8 test: no query ever embeds with model A and
  searches model B's collection.
- Cost guard: the admin sees the number of items to re-embed before activating; cloud
  re-embeds are rate-limited (`llm.reembed_per_minute`).

### 3.5 Speech-to-text over API

- `TranscriptionEngine` protocol with two adapters: **API** (OpenAI-style
  `/v1/audio/transcriptions`, multipart; e.g. a self-hosted whisper server, vLLM, OpenAI,
  Groq, Mistral Voxtral) and **built-in** faster-whisper (optional, only if its model is
  present).
- The audio is split into ≤ 10 min segments (providers cap request size), sent with
  `language=pl`, and the segments' timestamps are stitched back together.
- Workers send the audio from the decrypted temp file; the request body never leaves through
  a URL fetch.

### 3.6 Documents and OCR over API

- Docling stays as the **parser**, with its ML models off: born-digital PDF, DOCX and ODT
  convert without any model.
- Pages without a text layer (scans, photos of documents) are rendered to images and sent to
  the **OCR** purpose: by default a VLM model through chat ("przepisz tekst ze strony…", schema
  output), or a dedicated OCR adapter where a provider offers one (e.g. Mistral OCR, checked in
  10.5). The OCR text goes through the injection scan like other derivatives.
- The built-in Docling OCR stays as an optional adapter when its models are present.

### 3.7 Image embeddings

- `ImageEmbedder` protocol with adapters: **API**, and **built-in SigLIP** (9.8 code, pinned,
  optional). The built-in SigLIP shows up as a provider „Wbudowany (SigLIP)” only if its model
  is present in the `models` volume.
- For self-hosting, a SigLIP/CLIP model can run behind an embedding server on the cooperative's
  machine and be added as an API provider.
- Multimodal embedding APIs are not standardised. 10.6 starts with a short spike to pick the
  first one or two request shapes to support (candidates: an OpenAI-style `/v1/embeddings` with
  image inputs, as served by self-hosted embedding servers and some clouds; Cohere; Voyage), and
  records the choice in this plan.
- Recognition (9.9) and atlas indexing use whichever is active; with none, „Rozpoznaj” is
  hidden and approved photos wait for indexing.

### 3.8 Compose and deployment profile

- The Ollama `llm` service moves behind a compose profile (`--profile ollama`). The default
  stack has no model server.
- `llm` as a **relay** (dev today) becomes a general option: `LLM_RELAY_TARGET=host:port` for
  an LM Studio on another machine reached over a VPN (WireGuard/Tailscale), so worker networks
  stay closed.
- `models-init` downloads only what is still built in (spaCy, the injection classifier); the
  optional built-ins are separate flags (`make models WHISPER=1 SIGLIP=1 DOCLING_OCR=1`).
- Workers join `llm-cloud` (proxy only). The `worker-media` memory limit drops to 1 GB without
  built-in Whisper or Docling OCR.
- **"API-only" profile, documented with measured RAM:** target ≤ 6 GB at peak, so the app fits
  an 8 GB VPS with no GPU (e.g. Mikrus 4.1 PRO); LXC caveats and the shared-IPv4 port issue go
  in `DEPLOYMENT_DOCKER.md`.

### 3.9 Admin UI (`/admin/llm`)

- Providers: a badge „Dopuszczony do danych prywatnych”, and the clear/un-clear dialog (P3
  fields, step-up). „Lokalny / Chmura” stays as the network-path badge.
- Routing: the new purposes; private purposes list only cleared providers' models, with an
  explanation for the ones that are not cleared.
- „Brakujące modele” checklist; a re-embed progress panel (items done / total, ETA, cancel
  before swap).

## 4. Chunks

### Chunk 10.1 — Clearance trust model
- Migration (provider clearance columns, clearance trigger, auto-clear of existing LOCAL
  providers), `llm.private_data_endpoints`, `PRIVATE_PURPOSES`, rename `force_local_llm` →
  `private_data` everywhere, gateway/config-service/snapshot checks, audit events, AGENTS.md rule
  4, ARCHITECTURE §1 and SECURITY text.
- **Tests:** unit — the gateway refuses an uncleared provider for every private purpose, even
  with a tampered snapshot; integration — clear needs ADMIN+MFA+step-up and an endpoint on the
  env list (else 409/403), the trigger refuses a routing row to an uncleared provider,
  un-clearing removes the purpose at once, migration auto-clears the dev LM Studio provider.

### Chunk 10.2 — "Not configured" instead of failure
- `ModelNotConfigured`, the `WAITING_FOR_MODEL` derivative state and re-queueing on
  activation, `/api/capabilities`, lazy collection creation, startup without `LLM_EMBED_DIM`
  or a model, „Brakujące modele” data, metric.
- **Tests:** the stack boots with an empty configuration (smoke); an upload with no VLM waits
  then completes after activation; interactive endpoints answer 503 `model_not_configured`; no
  call reaches any other provider (respx).

### Chunk 10.3 — Versioned collections and safe re-embedding
- Collection versions + aliases, model/dimension recorded per version, re-embed job via the
  outbox, alias swap, grace-period cleanup, consent fan-out, audits on both versions, dimension
  taken from the probe.
- **Tests:** P8 (no mixed spaces); a consent revoke during re-embed is honoured in the new
  version; cancel before swap leaves the old version serving; an interrupted re-embed resumes;
  the existing `reindex` CLI maps onto the new mechanism.

### Chunk 10.4 — Speech-to-text over API
- TRANSCRIBE purpose + capability + probe, API adapter (multipart, segmenting, timestamp
  stitching), built-in faster-whisper as an optional adapter, worker egress via proxy.
- **Tests:** respx — a 25-min file becomes 3 requests with correct offsets; a provider 4xx is
  permanent, 5xx/429 retry; private-purpose enforcement; smoke with a self-hosted
  OpenAI-compatible server if available, else skipped.

### Chunk 10.5 — Documents: Docling without models, OCR over API
- Docling configured with no ML models, page rendering for pages without text, OCR purpose via
  VLM chat + an optional dedicated OCR adapter (decided in this chunk), built-in OCR optional.
- **Tests:** born-digital PDF/DOCX/ODT parse with no model present; a scanned page goes to
  OCR; OCR output injection-scanned; "not configured" leaves scans waiting while text pages
  complete.

### Chunk 10.6 — Image embeddings: API or built-in
- Spike (record the chosen request shapes here), `ImageEmbedder` adapters, IMAGE_EMBED purpose
  + probe, built-in SigLIP as an optional provider, atlas index on versioned collections (10.3),
  recognition off when none.
- **Tests:** the probe detects dimension and similarity order; switching SigLIP → API re-embeds
  the atlas; recognition hidden and indexing waiting with no model; benchmark CLI works with
  either embedder.

### Chunk 10.7 — Compose: no model server by default, API-only profile
- `ollama` profile, generalised `llm` relay, `models-init` flags, workers on `llm-cloud`,
  reduced memory limits, `DEPLOYMENT_DOCKER.md` API-only profile with measured RAM, `.env.example`.
- **Tests:** smoke — default stack has no model server and is healthy; workers reach a cloud
  host only via the proxy; measured peak RAM recorded in the doc.

### Chunk 10.8 — Admin UI, transparency page, E2E, security review, docs
- `/admin/llm` clearance dialog, new purposes, missing-models checklist, re-embed panel;
  members' „Gdzie przetwarzane są Twoje dane” + banner; capability-driven UI states.
- **Tests:** Vitest for the dialog, routing filters and disabled states; Playwright scenario
  12: start with nothing configured → features show „Model nie jest skonfigurowany” → admin
  adds LM Studio, clears it, routes purposes → a waiting upload completes → admin switches
  the embedding model → re-embed swaps; SECURITY_REVIEW "Stage 10 additions" (P1–P9).

**Stage 10 exit criteria:** the default stack runs with no model on the server; every purpose
works through an API provider configured in the UI; P1–P9 covered by tests; scenario 12 passes
against the compose stack.

## 5. Decisions taken (change before 10.1 if you disagree)

| Topic | Decision | Alternative |
|---|---|---|
| Who may clear a provider | Operator env list (ceiling) + ADMIN with MFA and step-up. | Admin alone (no operator ceiling; one compromised admin sends private data anywhere). |
| Existing installations | LOCAL providers on the env list are auto-cleared, so nothing changes on upgrade. | Start uncleared (private features stop until an admin acts). |
| Text embeddings | One EMBED purpose, private; wiki and private notes share one model. | Separate public/private embedding purposes (two vector spaces, two re-embeds, more config). |
| Members' consent | Transparency page + banner; no per-member opt-in. | Per-member opt-in to cloud processing (private features off for those who decline; complex). |
| OCR default | A VLM model through chat; dedicated OCR APIs as optional adapters. | Only dedicated OCR APIs (fewer providers, better on hard scans). |
| Injection classifier | Stays built in (CPU). | An LLM judge via the gateway (can be argued with; adds latency and cost). |
| Built-in models | Whisper, Docling OCR and SigLIP stay available as optional built-in adapters. | Remove them entirely (smaller images; self-hosting then needs a separate server). |

## 6. Open questions for the product owner

1. **Members' consent:** is a transparency page + banner enough, or should members be able to
   opt out of cloud processing of their private notes? (Default above: page + banner.)
2. **First cloud provider to support end to end:** Mistral (chat, embeddings, Voxtral
   transcription, OCR, all in the EU)? (Default: yes, as the reference cloud provider in tests
   and docs, with LM Studio as the reference local one.)

---

## Implementation notes

**Open questions (§6), 2026-10-22:** implementation started on the defaults: transparency page +
banner (no per-member opt-out); Mistral as the reference cloud provider, LM Studio as the
reference local one.

### 10.1 — Clearance trust model
- **Data:** migration `0019_provider_clearance` adds `private_data_cleared`, `cleared_region`,
  `cleared_retention`, `cleared_contract_ref`, `cleared_by`, `cleared_at` to `llm_provider`, with a
  CHECK that a clearance carries region, retention, contract and time (P3). Existing LOCAL
  providers are cleared by the migration. The three triggers of 0012 are redefined: private
  routes and ACTIVE snapshots need a cleared provider; a cleared provider cannot change its
  endpoint (un-clear first). The old "LOCAL cannot turn CLOUD" rule is gone: locality now only
  decides the network path.
- **Settings:** `llm.private_data_endpoints` (`LLM_PRIVATE_DATA_ENDPOINTS`, compose default
  `null` = the local endpoints). `endpoints.may_receive_private_data` checks the origin.
- **Gateway:** `ALWAYS_LOCAL` → `PRIVATE_PURPOSES`; `force_local_llm` → `private_data` everywhere;
  `LocalModelUnavailable`/`LocalModelRejected` → `ModelUnavailable`/`ModelRejected` (any
  deployment that fails now raises them, cloud included; promotion error codes became
  `model_unavailable`/`model_rejected`). `Deployment.private_data` (with `.cleared`) is set from
  the live provider row plus the env list when a process loads the configuration, so a tampered
  snapshot or a clearance outside the list counts for nothing.
- **Un-clearing at once:** the service bumps Redis `llm:config:clearance`; the config store
  reloads when it changes (or within the TTL without Redis) and re-resolves when the snapshot's
  providers' clearance or URL changed, even under the same revision number.
- **Probes:** embedding probes now run under `PLAYGROUND` with `private_data=False` (synthetic
  sentences), so a cloud embedding model can be probed before it is cleared; the
  "embeddings_are_local_only" refusal is gone, and cloud providers may have EMBED models.
- **Seeding from `.env`:** the local provider is cleared when its endpoint is on the list; if it
  is not, only RAG is routed (private purposes stay off).
- **API:** `POST /admin/llm/providers/{id}/clearance` (ADMIN + MFA + recent login, else 403
  `reauthentication_required`), `DELETE …/clearance` (no step-up: it only narrows). Provider,
  route, purpose, preset and status outputs carry `private_data*` fields instead of `local_only`.
- **UI (moved forward from 10.8 so that new local providers stay usable):** „Dane prywatne”
  column, „Dopuść do danych prywatnych” dialog with step-up notice, „Cofnij dopuszczenie” with a
  warning about stopped features; routing offers private purposes only cleared providers'
  models; playground private presets likewise. E2E scenario 9 clears its new provider.
- **Docs:** AGENTS.md rule 4 rewritten as approved; ARCHITECTURE §1, README, `.env.example`.

### 10.2 — "Not configured" instead of failure
- **Gateway:** `ModelNotConfigured(ModelUnavailable)` (with `.purpose`) whenever a purpose has
  no usable deployment: not routed, not cleared for private data, or above the cloud ceiling. It
  replaces the plain `LLMError` for public purposes, so existing handlers keep working.
  `gateway.configured(purpose)` answers the same question without a call or metrics.
- **Capabilities:** `llm/capabilities.py` maps features to purposes (`wiki_answers`,
  `notebook_answers`, `notebook_search`, `promotion`, `image_descriptions`, `recognition`).
  `GET /capabilities` (any signed-in member) returns booleans only. `/admin/llm/status` adds
  `missing_purposes`; gauge `llm_purpose_configured{purpose}`.
- **Interactive features:** promotion and „Rozpoznaj” check up front (`require_feature`) and
  answer 503 `model_not_configured` without creating anything; `/notes/ask` and `/rag/ask` map
  `ModelNotConfigured` to the same answer (no `Retry-After`: retrying won't help). A promotion
  whose model disappears after it was queued fails at once as `model_not_configured`.
- **Waiting work:** migration `0020_model_wait` adds `derivative_status.WAITING_FOR_MODEL` and the
  `model_wait` table (subject id, owner id, purpose, task: ids only, no RLS, like the vector
  outbox). No database role bypasses RLS, so this table is how the resume task finds work across
  members. Image descriptions without a vision model (also `.env` mode's
  `UnavailableVisionEngine`) and index jobs without an embedding model are parked there instead
  of failing or retrying.
- **Resume:** `workers/waiting.ModelWaitResumer` (task `index.resume_waiting`, worker-core) runs
  every 60 s from beat and right after an admin activates or rolls back a revision or clears a
  provider (`ProcessingQueue.model_config_changed`). For each purpose that now has a model it
  flips waiting derivatives back to PENDING in the owner's RLS session, sends the task again and
  deletes the row (send first: a lost delete only means a harmless second send). Gauge
  `model_wait_backlog{purpose}`.
- **Frontend:** `useCapabilities`/`useFeature`, a shared `ModelMissing` notice; the chat, the
  notebook question form, the promotion dialog and „Rozpoznaj” are disabled with it when their
  feature is off; materials show „Czeka na model AI” (no polling while waiting); `/admin/llm`
  shows „Brakujące modele”. Texts that promised "local only" now say "models cleared for
  private data".
- **Deferred to 10.3:** starting without `LLM_EMBED_DIM` and creating collections lazily belong to
  the versioned collections, which record each version's dimension.
- **Tests:** unit (gateway, capabilities, vision waits), integration (an image waits and resumes
  only once a vision model is configured; index job parked; `/capabilities`; 503s; admin
  `missing_purposes`); Vitest (chat off, waiting material, admin card, error mapping). The default
  test settings now name three placeholder models so features are on; no test calls them.

### 10.3 — Versioned collections and safe re-embedding
- **Registry, not aliases:** `index_generation` (migration 0021, no content, no RLS) records each
  version of the text index: the embedding model (`embed_key` = provider/model@endpoint, the
  snapshot deployment without secrets, or `None` for `.env`'s model), the **probed** dimension,
  its two collections (`<family>__g<n>`), status (BUILDING / SERVING / RETIRED / CANCELLED, at
  most one SERVING and one BUILDING by partial unique indexes), progress (`total`, `done`,
  `failed`, `cursor`) and the grace-period timestamps. Deviation from §3.4: the application never
  reads through a Qdrant alias. A query has to be embedded with the searched generation's model
  anyway (P8), so the reader needs the registry, and an alias would be a second source of
  truth. The family name is still kept as an alias of the serving generation for operators
  (`collections.point_aliases`) once no adopted collection holds that name.
- **P8 in code:** `vector.embedding.Embedder` only embeds *with a generation*
  (`Generation.deployment`): `query()` returns the vectors together with the generation they may
  search (`Query`), and RAG / notebook search use that generation's collection. The gateway's
  `embed(..., deployment=)` pins the model instead of routing EMBED and applies the same policy
  (cloud ceiling, clearance; refused → `ModelNotConfigured`, never another model). A
  generation's model is resolved live like an ACTIVE revision (`config_store.resolve`): provider
  row, endpoint re-classified, clearance re-checked; un-clearing or disabling the provider makes
  its generation unusable at once.
- **Writes:** indexing writes to every writable generation (SERVING + BUILDING), each with its own
  model, under the existing FOR SHARE locking. Consent and governance operations from the outbox
  go through `CanonicalFanout` to every live generation, read fresh at apply time (RETIRED ones
  too until dropped: a process that cached the old generation for a few seconds must not find
  revoked content there). Note and photo deletions use `PrivateFanout`. The nightly consistency
  audit checks every live generation.
- **Reconcile** (`vector.reembed.GenerationManager`, run by `index.resume_waiting` every minute
  and after activate/rollback/clearance): no generation yet → adopt the pre-10.3 collections as
  generation 1 if their size matches the probe, else create one; EMBED routed to another model →
  create a BUILDING generation (dimension from a probe of that model, collections created then:
  lazy, so startup needs neither `LLM_EMBED_DIM` nor a model); configuration back on the
  serving model → cancel the build; drop CANCELLED generations at once and RETIRED ones after
  `qdrant.retired_grace_hours` (24). A build an admin cancelled is not restarted for the same
  revision.
- **Build** (`ReEmbedder`, task `index.reembed`, worker-core): every CANONICAL article, then each
  member's DONE derivatives (as the owner, RLS), in id order through the same `Indexer` code,
  restricted to the BUILDING generation; the cursor is saved every 20 items and at the end of a
  240 s run, which then sends itself again (interrupted runs resume from the cursor; a try-lock
  keeps one run at a time). Model down → stop and wait for the next reconcile. At the end the
  generations swap in one transaction, aliases are pointed and `index.refresh_clusters` is sent
  (the semantic detector's vector space changed; its per-user history drops entries of another
  dimension). Cost guard: `llm.reembed_per_minute` (600) for models not on a local endpoint;
  `GET /admin/llm/index` shows the generations and how many items a switch re-embeds.
- **Activation:** the `embed_change_requires_reindex` / `embed_dimension_mismatch` refusals are
  gone; probes report any dimension. `qdrant.vector_size` and `LLM_EMBED_DIM` are removed;
  bootstrap creates only the atlas collection (images move to generations in 10.6).
- **`reindex` CLI:** `GenerationManager.start` + `ReEmbedder` to completion: a new generation for
  the active model, swapped in when complete; nothing is dropped up front and pending outbox
  operations are no longer superseded (they apply to every generation).
- **Admin API:** `GET /admin/llm/index`, `POST /admin/llm/index/rebuild`,
  `POST /admin/llm/index/{n}/cancel` (audited: `INDEX_GENERATION_STARTED/SWAPPED/CANCELLED`).
  The progress panel is 10.8.
- **Limits:** in `.env` mode only one embedding model exists, so after changing
  `LLM_EMBED_MODEL` the old generation cannot be queried until the new one is built (search
  answers `model_not_configured` meanwhile). Semantic-anomaly history from the old model with
  the same dimension stays until it expires (scores only, never retrieval).
- **Tests:** unit (pinned deployment obeys clearance and the cloud ceiling, no fallback; embedder
  per generation; dimension check; semantic history); integration with real Postgres + Qdrant
  (adoption; P8 before and after the swap with two vector spaces; an interrupted build resumes;
  a consent change during the build reaches both generations; cancel keeps the old one serving;
  rolling the model back cancels the build; un-clearing stops the generation; drop after grace
  and alias; a first model builds and then serves; `reindex` builds and swaps; admin endpoints).
