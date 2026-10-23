# Stage 6 — Frontend, Web Security Headers & Final Audit

**Goal:** a Next.js UI over the BFF backend, hardened against XSS/clickjacking/mass copying, an
admin security console, and an automated security and E2E verification suite.
**Spec refs:** `PLAN.md` Etap 6 · `AGENTS.md` §1 (Next.js App Router, TS, Tailwind, BlockNote/TipTap)

> Frontend checks per chunk: `npm run lint`, `npm run typecheck` (`tsc --noEmit`, `strict: true`),
> `npm test` (Vitest + Testing Library). The Python checks stay mandatory whenever backend code changes.

---

## Chunk 6.1 — Next.js scaffold, BFF proxy, security headers & CSP

- `npx -y create-next-app@latest frontend --ts --app --eslint --tailwind --src-dir --import-alias "@/*" --use-npm`
  (run `--help` first). Tailwind **v4** unless told otherwise (gap G20).
- **Frontend Dockerfile** (`frontend/Dockerfile`):
  - Multi-stage build (`node:22-alpine` builder with `output: "standalone"` in `next.config.ts`).
  - Runner: `gcr.io/distroless/nodejs22-debian12` (or `node:22-alpine` nonroot), running `server.js` on port 3000.
  - Attached to `edge` network (`internal: true`).
- **Same-origin via Caddy (no Next.js rewrites needed):**
  - Caddy routes `/` to `frontend:3000` and `/api/*` to `api:8000`. Single origin means cookies stay
    `__Host-` + `SameSite=Strict` and browser never talks to Keycloak tokens.
- API client `src/lib/api.ts`: typed via `openapi-typescript` generated from FastAPI `/openapi.json`
  (script `npm run gen:api`). It adds the `X-CSRF-Token` header from the `__Host-csrf` cookie and handles
  `401 {challenge}` (step-up / PoW flows, 5.6) and `423/403 security_lock`.
- **Security headers** in `middleware.ts` (per-request nonce):
  - `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-{n}' 'strict-dynamic'; style-src 'self' 'nonce-{n}'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; require-trusted-types-for 'script'; trusted-types dompurify nextjs#bundler default`
  - `X-Frame-Options: DENY`, `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`,
    `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`,
    `Permissions-Policy: camera=(), microphone=(self), geolocation=()`, `Cross-Origin-Opener-Policy: same-origin`.
- FastAPI gets the same headers on API responses (`SecurityHeadersMiddleware`), so direct API hits are covered too.
- Design system: Tailwind theme tokens (cooperative palette, dark mode), `Inter`/`Outfit` via `next/font`,
  base components (Button, Card, Badge, Dialog, Toast, StatusPill for article/consent states).

**Tests:** Vitest for the API client CSRF/challenge handling. Header assertions are a Playwright test (6.7).

---

## Chunk 6.2 — Private notebook UI

- Routes: `/notebook`, `/notebook/[noteId]`.
- Note editor: **BlockNote** (built on TipTap/ProseMirror). Autosave with debounce.
- Attachments: drag-and-drop upload with progress, a `keep_location` checkbox ("Zachowaj lokalizację GPS")
  that's off by default with an explanation, per-attachment status (scanning → processing → ready / rejected
  with reason 415/422).
- Add link dialog (SSRF errors shown in a friendly way).
- Derivatives panel (transcript, image description, parsed doc). Quarantined items are highlighted with
  the flagged spans and an explicit "confirm safe" action.
- "Ask my notebook" side panel (private RAG).
- Multi-select notes → **"Utwórz wniosek do Wiki"** → `promote-to-wiki`, then a progress/status toast.

**Tests:** component tests (upload states, quarantine confirmation), mock API via MSW.

---

## Chunk 6.3 — Wiki & expert review panel

- `/wiki`, `/wiki/[articleId]`: canonical articles. Suspended blocks show as "Treść wstrzymana przez autora".
  Status badges, last verified date, authors, approving experts, a "superseded by" banner.
- Block-level authorship: hover/side gutter shows the author of each paragraph (BlockNote read-only
  view with custom block attributes, per ARCHITECTURE §5).
- Anti-mass-copy measures for the presentation layer (deterrence only, server-side shield is the real
  control): render blocks lazily on viewport (feeds coverage counting server-side), no bulk "export",
  invisible per-user watermark in rendered text (same Watermarker on Wiki render, optional flag).
- `/review` (EXPERT, MFA step-up enforced by API): queue, diff vs. superseded version, decision form
  (approve / request changes / reject / mark disputed / supersede picker), `REVISION_REQUIRED` alerts on top.
- `/me/drafts`: author drafts → submit for review.

**Tests:** role-based route guards, decision form validation.

---

## Chunk 6.4 — Creator IP (contributions) panel

- `/me/contributions`: table of articles/blocks authored, current consent and article status.
- Toggles per article and globally: **Wstrzymaj (Suspend)** / **Wznów (Resume)**. **Usuń trwale (Revoke)**
  goes through a confirmation dialog: type `REVOKE`, consequences explained (co-authored articles go back to
  review), step-up auth if `auth_time` is stale.
- Optimistic UI is **not** used for revoke (wait for the server result). History of consent changes
  (from the audit log, user's own events only).

**Tests:** confirmation flow, step-up redirect on 401 challenge.

---

## Chunk 6.5 — Cooperative chat (RAG) UI with safe Markdown

- `/chat`: conversation view. The answer is rendered from `answer_markdown` with **citations as cards**
  (title → Wiki link, last verified date, authors, approving experts). The "Brak oficjalnego stanowiska
  ekspertów" state has distinct styling.
- **Safe Markdown pipeline:** `react-markdown` + `remark-gfm` with **`rehype-raw` disabled** (no raw HTML),
  then `rehype-sanitize` with a strict schema, and **DOMPurify** (`isomorphic-dompurify`, Trusted Types policy
  `dompurify`) for any path that produces HTML strings (Wiki block render). Links: only `https:` and
  same-origin, plus `rel="noopener noreferrer nofollow"`. Images from LLM output are disabled.
- Challenge handling: PoW solved in a **Web Worker** (`crypto.subtle.digest`) with a progress indicator.
  MFA step-up redirects to login with `acr_values`. Lock state is a full-screen notice with a contact-admin message.

**Tests (XSS):** Vitest renders hostile payloads (`<img src=x onerror=…>`, `[x](javascript:alert(1))`,
`<svg><script>`, `<iframe>`, data-URI links). Assert no executable output.

---

## Chunk 6.6 — Admin security dashboard (+ backend endpoints)

**Backend (ADMIN + MFA, read-only, audited access):**
- `GET /admin/audit-log?event_type&actor&from&to&cursor` (keyset pagination), `GET /admin/audit-log/verify`
  (hash-chain status, 1.4).
- `GET /admin/coverage`: per user: 24h/7d/30d ratios, risk level, semantic score. Top-N risky users.
- `GET /admin/alerts`: honeypot triggers, coverage locks, verbatim blocks, refresh-token reuse.
- `POST /admin/users/{id}/unlock` (with reason, audited), `GET/POST /admin/honeypots` (5.4).

**Frontend** `/admin/security`: KPI cards, coverage distribution chart (Recharts), alerts timeline,
filterable audit-log table, user drill-down with risk history, unlock action.

**Tests:** backend authz matrix (MEMBER/EXPERT → 403, ADMIN without MFA → 403); frontend table/filter tests.

---

## Chunk 6.7 — Security scanning & Playwright E2E suite

**Static & dependency scanning** (CI job `security`, also `make audit`):
- `uvx pip-audit` against the exported lock (`uv export --format requirements-txt | uvx pip-audit -r /dev/stdin`)
  (gap G11: `uv pip audit` doesn't exist).
- `uvx bandit -r src -ll` (ruff `S` rules already run on every commit).
- `npm audit --audit-level=high` in `frontend/`. `gitleaks` for secrets. `trivy` image scan of built containers.
- Optional DAST: OWASP ZAP baseline against the compose stack.

**Playwright E2E** (`frontend/e2e/`, runs against `docker compose` with Keycloak test users, local
fake/small LLM profile):
1. **Auth & roles:** member1 login, `/review` forbidden. expert1 without OTP gets the step-up prompt. With OTP, the review is accessible.
2. **Notebook isolation:** member1 creates a note and copies its URL. member2 opening it gets 404.
   Direct API fetch with member2 cookies → 404.
3. **Upload security:** exe-as-pdf → error toast (415). JPEG with GPS → stored copy has no GPS.
4. **Push-to-Wiki → review → canonical → chat answer with citation** (full happy path).
5. **Exit strategy:** co-author revokes → chat no longer cites the article; it shows in the review queue with the alert.
6. **Mass extraction lockout:** a scripted loop of chat queries from member2 ends in step-up/lock before
   coverage threshold; the admin dashboard shows the alert and the audit entry.
7. **Headers/CSP:** assert all security headers present on HTML and API responses. An inline-script injection attempt is blocked (CSP violation captured).
8. **XSS:** a Wiki article containing hostile Markdown renders inert.

**Stage 6 exit criteria / release gate:** all scanners show no high/critical issues (or documented
accepted risks), all E2E scenarios green, audit-chain verifier clean. Final `SECURITY_REVIEW.md` maps
each OWASP API Top 10 and OWASP LLM Top 10 (2025) item to the implementing chunk and test.

---

## Chunk 6.8 — Production deployment on Linux server (registry, deploy, backup/restore)

**Goal:** a production-grade operational workflow allowing the cooperative to ship images, run zero-downtime/low-downtime upgrades, and perform verifiable disaster recovery backups on a standalone Linux server.

**Deliverables:**
- `docker-bake.hcl`: multi-target, multi-arch build configuration (`linux/amd64` and `linux/arm64`) for:
  - `spoldzielnia/api`
  - `spoldzielnia/worker-core`
  - `spoldzielnia/worker-media`
  - `spoldzielnia/frontend`
- CI publishing workflow (`.github/workflows/publish.yml`): builds and pushes tagged images to GHCR (or private registry) on git tags (`v*`).
- `compose.prod.yaml`:
  - Image tags pointing to registry instead of local build contexts.
  - Caddy configured for automatic ACME TLS via Let's Encrypt for public domain.
  - `restart: unless-stopped`, resource limits (CPU/memory), logging logrotate options.
  - LLM service running containerized `ollama/ollama` (with GPU support via `compose.gpu.yaml`).
- Deployment script `infra/scripts/deploy.sh` (`make deploy VERSION=vX.Y.Z`):
  1. Pulls target version images.
  2. Runs DB migrations (`docker compose run --rm migrate`).
  3. Restarts services with `--wait`.
  4. Runs post-deploy smoke checks (`infra/scripts/smoke.sh`).
- Backup service & script `infra/scripts/backup.sh` (`make backup`):
  - Dumps PostgreSQL (`pg_dump -Fc`) for `spoldzielnia` and `keycloak` databases.
  - Mirrors MinIO encrypted object buckets (`mc mirror`).
  - Encrypts backup bundle using `age` (asymmetric public key encryption).
  - Backs up KEK and configuration secrets.
  - Verifies Qdrant reproducibility via `spoldzielnia reindex`.
- Restore runbook `docs/ops/DISASTER_RECOVERY.md`:
  - Documented and tested step-by-step restoration from bare metal Linux server to fully operating instance.
  - Verification of audit log hash chain (`spoldzielnia audit-verify`) and vector drift audit.

**Tests:**
- In CI: automated rehearsal of `infra/scripts/backup.sh` followed by test restoration into a fresh container environment, validating data integrity and hash chain validity.

---

## Implementation notes (as built)

- **Next.js 16**: request interception is `src/proxy.ts` (the former `middleware.ts`). Cache
  Components / partial prerendering stay off: a prerendered shell can't carry the per-request
  nonce. Unmatched URLs go through a catch-all route to `app/not-found.tsx`, because Next's
  built-in 404 page uses inline style attributes the CSP blocks.
- **Trusted Types**: a `default` policy installed from `src/instrumentation-client.ts` (before
  any chunk loads). Script URLs: only same-origin `/_next/static/`, returned unchanged (Next
  finds chunks by exact `src`). HTML sinks are sanitised with DOMPurify rather than refused
  (BlockNote's paste and Markdown conversion use them). Script strings stay blocked. Chrome
  reports a CSP violation for each inline style or handler attribute DOMPurify meets while
  parsing in its inert document; nothing is applied, and DOMPurify strips it.
- **Editor styles**: TipTap/BlockNote inject `<style>` elements; they get the request nonce
  (`NonceProvider` → `_tiptapOptions.injectNonce`). Ariakit is used instead of Mantine, which
  injects runtime styles.
- **Note bodies are Markdown** (BlockNote's lossy export), so the backend's PII scrubbing and
  promotion keep working on text. "Ask my notebook" searches the private index, which holds
  processed attachments and links, not typed note bodies.
- **Safe Markdown** (`SafeMarkdown`) was built in 6.3 for Wiki blocks and is reused by the chat.
- **Wiki rendering**: no lazy, on-viewport rendering of blocks. The article endpoint returns
  all visible blocks (and counts them for coverage) in one response, so revealing them lazily
  in the browser would hide nothing. The per-reader Wiki watermark exists behind
  `shield.watermark_wiki` (default off: it rewords expert-approved text).
- **Approving experts** were added to `ArticleOut` (approvals up to `last_verified_at`).
- **Chat proof-of-work** runs in a module Web Worker (`new Worker(new URL(...))`). Turbopack
  emits it under `/_next/static/`, so the Trusted Types policy allows its URL and the CSP's
  `worker-src 'self' blob:` its execution. The solver lives in `pow-core.ts`, imported by the
  worker and by `pow.ts`: when the worker imported the module that creates it, `next build`
  never finished. Verified in the stack: 401 challenge → 18-bit puzzle solved in the browser →
  answer, no CSP violations.
- The conversation is kept in page memory only; nothing of it is stored by the frontend.
- **Admin charts are hand-written SVG**, not Recharts: two small charts didn't justify the
  dependency, and Recharts draws with inline styles that would need loosening the CSP. The
  series hue was validated (contrast, band) against the app's light and dark surfaces; risk
  levels use the fixed status palette and always carry a label beside the coloured mark.
- **Risk in the console** is incidents + coverage pressure, the parts that persist. The
  semantic score exists per request only, so it shows up in the per-account history of
  shield decisions rather than in the table.
- **Every console read is audited** (`SECURITY_DASHBOARD_ACCESSED`, view + filters), and
  unlocking records the admin's reason and the previous lock reason (`ACCOUNT_UNLOCKED`).
- **Scanners (`make audit`, CI job `security`)** use `uvx pip-audit` on `uv export`, `uvx bandit -ll`,
  `npm audit --omit=dev` (dev findings are printed for information), and gitleaks and trivy pinned by
  digest. Findings that are accepted carry their reason in `docs/SECURITY_REVIEW.md` § Accepted
  risks and in `.trivyignore`. The frontend runtime moved to distroless Debian 13 because Debian
  12's `libssl3` had fixed HIGH CVEs.
- **The E2E suite** (`make e2e`, Playwright, serial) runs against the real compose stack, with no mocks.
  Logins go through Keycloak, and TOTP codes come from the dev seed. Each login waits for a fresh
  30-second window because Keycloak refuses a replayed code. Data the UI can't create (canonical
  multi-author articles, GPS JPEGs, a shield reset) is seeded by `e2e/support/seed.py`, piped into
  the `api` container. `expert2`, the second reviewer the quorum needs, is in the realm export and
  also created by global setup on stacks imported before it existed.
- **Scenario 4 depends on the embedding model.** Global setup measures the cosine of two unrelated
  texts. If it is ≥ 0.8, the model can't rank, so the scenario is skipped with that reason rather
  than failing at random. The same check is in `make smoke`. Making a 11B local model
  (`LLM_STRUCTURED_OUTPUT=prompt`) answer reliably needed gateway changes: prompt mode now shows an
  example and per-field notes instead of the raw JSON schema (the model echoed the schema),
  replies nested under `"properties"` are recovered, citations listed only in the `citations`
  field get markers appended, and runs of repeated markers are collapsed.
- **Scenario 5** asserts the exit strategy through the API as well as the UI: after the co-author's
  revoke, the article is `REVISION_REQUIRED`, the revoked block has gone, and no answer cites it.
- **Scenario 6** shows the step-up → lock path (401 challenges ignored, then 403, and then `/me` 423)
  and the admin unlock through the UI. The coverage-ratio cut-off needs a larger corpus than the dev
  stack's (the floor from G14 dominates), so `test_distillation_attack.py` covers it.

### 6.7 closed (2026-10-23)

- **The embedding check passes.** It failed because of the dev stack's model, not the code.
  LM Studio's GGUF build of Qwen3-Embedding-4B maps every text to nearly the same vector:
  cosine 0.944 between the two unrelated smoke sentences, 0.975 for a related pair.
  EmbeddingGemma (`text-embedding-embeddinggemma-2`, 768 dimensions, multilingual) separates
  them (0.56 against 0.86; the app's own probe gives 0.687). It was routed to EMBED the normal
  way: model added, probed, draft, activation (revision 25). Worker-core built index generation 5
  with it and swapped it in (10.3). `.env` follows (`LLM_EMBED_MODEL`, `LLM_EMBED_DIM=768`).
  `make smoke` now passes 44/44.
- **Scenario 4 runs instead of skipping, and passes.** Two things kept it from passing on reruns:
  - Earlier runs had left canonical articles on the same topic, under the same heading, which
    competed with the new one for the citation. The new seed action `retire_topic` supersedes
    them first, through the governance transition and the vector outbox.
  - The chat question now names the article's title.
- **Shield state from earlier runs.** Global setup now resets member1's and member2's shield
  state before every run. Before, a day of reruns raised their risk until `/rag/ask` demanded
  proof of work (scenario 5 got 401).
- **Atlas recognition timeout.** The local 12B vision model sometimes needs a retry to produce
  valid JSON, which took 194 s once. The wait is now 7 minutes.
- **Scanners:** Pillow 12.3.0 had 35 advisories (fixed in 13.0.0; it decodes untrusted uploads).
  Pillow is now 13.0.0, along with certifi 2026.10.23 and docling 2.142 (the version that
  allows Pillow 13). Bandit's B608 on the trigger DDL in migrations 0012 and 0019 (module
  constants only) is annotated and listed under accepted risks.
- **Also from the scans:** gitleaks flagged a made-up password in a Vitest fixture, now accepted
  in `.gitleaksignore`. Trivy found a stale `libexpat1` (deb12u4, CVE-2025-59375) in
  worker-media; it came from a cached apt layer of a local build, and a fresh build has deb12u5.
- **Release gate, 2026-10-23, MFA on, on images rebuilt with Pillow 13:**
  - `make audit` clean;
  - `make smoke` 44/44;
  - Playwright 24/24 tests (12 spec files, none skipped);
  - Vitest 156/156;
  - the audit chain verified in the DR rehearsal.
