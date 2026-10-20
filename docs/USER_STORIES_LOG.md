# User Stories Log — work beyond the original 6-stage plan

This log records user stories and scope additions that are **not part of the original plan**
(`PLAN.md`, Etapy 1–6). Each story gets an ID, its source, status and links to the
implementation plan, chunks and commits.

**Statuses:** `PROPOSED` (captured, not planned) · `PLANNED` (implementation plan exists) ·
`IN PROGRESS` · `DONE` (all chunks ticked, tests green) · `DROPPED`.

**How to add an entry:** append a new `US-NN` section, add a row to the summary table, and, once
planned, add its chunks to `PLAN.md` under *Rozszerzenia poza pierwotny plan*. When a chunk is
committed, add the commit hash to the story's *Delivery* list.

## Summary

| ID | Story | Status | Plan | Added |
|----|-------|--------|------|-------|
| [US-01](#us-01) | Administrator configures LLM models (local / cloud) in the UI and tests them | DONE | [07_LLM_MODEL_CONFIG.md](implementation/07_LLM_MODEL_CONFIG.md) · Etap 7 | 2026-10-20 |

---

## US-01

**Story:** As an administrator I would like to be able to configure LLM models in the UI, so that
I can decide whether to use locally hosted or cloud models and test different models with an easy
way to change the configuration.

- **Source:** product owner request, 2026-10-20.
- **Status:** DONE 2026-10-20 (Etap 7, chunks 7.1–7.7). Implemented on request while 6.7 and
  6.8 are still open.
- **Plan:** [docs/implementation/07_LLM_MODEL_CONFIG.md](implementation/07_LLM_MODEL_CONFIG.md)
- **Acceptance criteria:**
  1. An admin (with MFA) can add, edit, test and disable local and cloud model providers in
     `/admin/llm`; API keys are write-only and stored encrypted.
  2. An admin can assign a model per purpose (RAG, promotion, private notes, PII scrubbing,
     vision, embeddings); purposes that handle private data can only use local models, enforced in
     the UI, the API, the database and the gateway.
  3. Cloud models can be used only if the operator allows it in the server configuration; the UI
     can narrow but never widen that policy.
  4. Changes are made in a draft revision, tested (connection + probe), activated without
     restarting services, and can be rolled back in one click.
  5. A playground compares up to 3 models side by side (answer, latency, tokens, JSON validity)
     without using any knowledge-base or private content.
  6. Every change and playground use is recorded in the security audit log.
- **Out of scope / follow-ups:** live switching of the embedding model (needs re-embedding of the
  index, candidate US-02); per-user model choice; cost reporting.
- **Delivery:**
  - 7.1 data model, encrypted keys, locality triggers, seeding: `068ff49`
  - 7.2 config store, hot reload, per-purpose gateway: `9fca464`
  - 7.3 `llm-egress` CONNECT proxy: `6be5a4e`
  - 7.4 `/admin/llm` API, connection tests, probes: `83e1b3f`
  - 7.5 playground and model comparison: `5849ac2`
  - 7.6 `/admin/llm` console: `d0173aa`
  - 7.7 E2E scenario 9, security review, docs: the `feat(stage-7.7)` commit

---

## Scope additions inside the original stages (not separate user stories)

Changes that went beyond the letter of the chunk specs while implementing Stages 1–6. They
are recorded here so the difference between the plan and the delivered system stays visible.

| Date | Commit | Stage | Addition | Why |
|------|--------|-------|----------|-----|
| 2026-10-19 | `7bd2b09` | 3.4 | `llm.structured_output = json_schema \| prompt`, tolerant JSON extraction, `LocalModelRejected` for non-retryable 4xx. | Live promotion with LM Studio + Bielik failed: its grammar sampler returns 400 for any `json_schema` response format. |
| 2026-10-19 | `b7a7f8b` | 6.7 | Prompt mode sends an example object + field notes instead of the raw schema, schema-echo recovery, citation marker clean-up. | Local models echoed the JSON Schema back instead of filling it. |
| 2026-10-19 | `b7a7f8b` | 6.7 | Second expert account (`expert2`) in the Keycloak realm export; smoke check that the embedding model separates unrelated texts. | Review quorum needs two experts in E2E; a broken embedding model silently ruined retrieval. |
