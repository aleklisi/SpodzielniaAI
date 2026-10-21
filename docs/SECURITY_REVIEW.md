# Security Review: Spółdzielnia AI (Stage 6 release gate)

This document closes Stage 6 (chunk 6.7). It maps each item of the **OWASP API Security Top 10
(2023)** and the **OWASP Top 10 for LLM Applications (2025)** to the chunk that implements the control
and the tests that prove it. Then it lists the accepted risks that the scanners are told to ignore.

Test locations: `tests/unit/…` and `tests/integration/…` (pytest), `frontend/src/**.test.ts(x)`
(Vitest), `frontend/e2e/N-*.spec.ts` (Playwright against the compose stack).

## How to re-run the gate

| Check | Command | Expected |
|---|---|---|
| Lint, types, unit and integration | `uv run ruff check . && uv run pyright && uv run pytest` | green |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test` | green |
| Scanners | `make audit` (pip-audit, bandit, npm audit, gitleaks, trivy) | `audit: clean` |
| Stack | `make build && make up && make smoke` | all checks OK |
| E2E | `make e2e` | 10 scenarios green |
| Audit chain | `spoldzielnia audit-verify` or `GET /admin/audit-log/verify` | `ok: true` |

## OWASP API Security Top 10 (2023)

| # | Risk | Control (chunk) | Proof |
|---|---|---|---|
| API1 | Broken Object Level Authorization | Postgres RLS on `private_notes` and their children, driven by `app.current_user_id` set per session; `FORCE ROW LEVEL SECURITY` binds the table owner too; another member's note is a 404, the same as a missing one (1.3, 1.4, 2.x) | `integration/test_rls_isolation.py` (whole file, including `test_setting_does_not_leak_to_pooled_connection` and `test_force_rls_binds_table_owner`); `test_upload_to_other_users_note_is_404`, `test_link_on_other_users_note_is_404`, `test_other_users_note_cannot_be_promoted`; `test_members_act_only_on_their_own_blocks`; `unit/test_rls_dependency.py`; E2E `2-isolation` |
| API2 | Broken Authentication | Keycloak OIDC with PKCE through a BFF; opaque `__Host-` session cookie, tokens encrypted at rest in Redis; refresh rotation; absolute lifetime; MFA (`amr` contains `otp`) for EXPERT and ADMIN; step-up `max_age` for revoke; lock drops sessions (1.2); admin user management (Stage 8): Keycloak keeps every credential, temporary passwords are generated server-side, shown once and never logged, audited or stored; a block disables the Keycloak user and ends every app and IdP session; new experts/admins must enrol TOTP | `unit/test_auth_sessions.py` (`test_login_sets_hardened_session_cookies`, `test_redis_holds_no_usable_secrets`, `test_absolute_lifetime_ends_session`, `test_expired_access_rotates_refresh_token`, `test_locked_user_is_423_and_loses_session`); `unit/test_auth_oidc.py`; `integration/test_keycloak_login.py`; `test_login_never_unlocks_a_locked_user`; `test_revoke_needs_confirmation_and_a_recent_login`; `unit/test_keycloak_admin.py::test_passwords_never_reach_the_logs`; `integration/test_user_admin.py` (`test_create_with_a_temporary_password`, `test_block_ends_every_session_and_unblock_needs_step_up`, `test_an_elevated_account_must_enrol_totp`); `integration/test_admin_users_api.py::test_block_ends_open_sessions_at_once`; `unit/test_auth_sessions.py::test_revoke_user_ends_every_family_of_that_user_only`; E2E `1-auth`, `10-user-admin` |
| API3 | Broken Object Property Level Authorization | Pydantic v2 request models with `extra="forbid"`; response models list their fields explicitly (no ORM passthrough); `owner_id`/`author_id` always come from the session, never the body; Qdrant chunks never leave the API (Anti-Distillation, 5.x) | `test_owner_cannot_hand_note_to_another_user`, `test_insert_for_another_owner_is_rejected`; `test_request_validation` (promotion); `test_normal_answer_is_synthesis_with_citations_only`; `test_committed_openapi_schema_is_current` (the client contract can't drift quietly) |
| API4 | Unrestricted Resource Consumption | Streaming upload size caps (413), zip and decompression bomb limits, per-category limits (2.1, 2.2); promotion daily quota (3.4); question length bounds; PoW at HIGH risk, coverage windows 24h/7d (5.x) | `test_oversized_upload_is_413_while_streaming`, `test_zip_bomb_is_422`, `test_decompression_bomb_image_is_refused`, `unit/test_file_validation.py`; `test_daily_limit`; `test_notebook_question_length_is_bounded`; `unit/test_coverage.py`; `test_high_risk_requires_proof_of_work_for_each_request` |
| API5 | Broken Function Level Authorization | Role dependencies (MEMBER / EXPERT+MFA / ADMIN+MFA) on every router; an expert can't decide on their own article; the UI's `RequireRole` is presentation only; user management grants only the three app roles, needs a fresh second factor for EXPERT/ADMIN grants and unblocks, and refuses self-demotion, self-blocking and removing the last admin; the Keycloak service account has `view-users`/`query-users`/`manage-users` only (1.2, 4.x, 5.x, 6.6, 7.4, 8.x) | `test_reviews_need_expert_role_and_mfa`, `test_reviewer_cannot_decide_on_own_article`, `test_quorum_needs_distinct_experts`; `test_admin_routes_need_admin_with_mfa`; `integration/test_admin_console.py::test_authorization_matrix`; `integration/test_admin_llm_api.py::test_authorization_matrix`; `integration/test_admin_users_api.py` (`test_authorization_matrix`, `test_privilege_grants_need_a_fresh_login`, `test_invalid_input_is_rejected`); `integration/test_user_admin.py` (`test_admins_cannot_lock_out_the_organisation`, `test_two_admins_demoting_each_other_at_once`); `integration/test_keycloak_admin.py::test_the_service_account_cannot_touch_the_realm`; E2E `1-auth`, `9-llm-config`, `10-user-admin` |
| API6 | Unrestricted Access to Sensitive Business Flows | The cooperative's core asset is the corpus: the anti-distillation shield (risk engine, HLL coverage, semantic anomaly, honeypots, verbatim verifier, watermarking, PoW, lock) (5.1–5.6); promotion quota; revoke with confirmation and step-up (4.x) | `integration/test_distillation_attack.py` (cut off before 15% of a 200-block corpus; a bot that pays the PoW still ends locked); `integration/test_shield_api.py`; `integration/test_honeypot.py`; `integration/test_verbatim.py`; `unit/test_watermark.py`; E2E `6-lockout` |
| API7 | Server Side Request Forgery | `safe_fetch`: scheme allow-list, DNS resolution with every answer checked against private, loopback, link-local and metadata ranges, the connection pinned to the validated IP, redirects re-validated per hop (2.3). Admin-entered LLM endpoints (7.1): only origins on the `.env` allowlists, no credentials/query in URLs, redirects not followed; cloud traffic only through `llm-egress` (CONNECT to allowlisted hosts on :443, every resolved address public, tunnel pinned to it), which workers cannot reach (7.3) | `unit/test_safe_fetch.py` (`test_any_private_answer_blocks_the_host`, `test_connection_is_pinned_to_validated_ip`, `test_redirect_to_metadata_is_blocked_at_hop_two`); `integration/test_links_api.py::test_ssrf_targets_are_blocked`; `unit/test_llm_endpoints.py`; `unit/test_connect_proxy.py`; `test_keys_are_write_only_and_urls_are_checked`; smoke "llm-egress refuses …", "worker-core cannot reach llm-egress" |
| API8 | Security Misconfiguration | Security headers on API and HTML; nonce CSP with `strict-dynamic` and Trusted Types; CSRF proof (double-submit bound to the session, plus an Origin check); OpenAPI and docs hidden in prod; secrets only from `/run/secrets`, never in `repr` or logs; distroless non-root images (1.1, 6.1, D-x) | `unit/test_security_headers.py`; `test_unsafe_requests_need_csrf_proof`, `test_csrf_tokens_are_bound_to_the_session`; `test_openapi_hidden_in_prod`; `unit/test_settings.py`, `test_rendered_json_never_contains_secret`; `frontend/src/lib/trusted-types.test.ts`, `api.test.ts`; E2E `7-headers`; `make audit` (trivy) |
| API9 | Improper Inventory Management | One API surface behind Caddy, with no versioned leftovers; the OpenAPI schema is committed and drift-tested, and the frontend types are generated from it; internal services (Postgres, Qdrant, Redis, MinIO, ClamAV) stay on internal networks: in `compose.yaml` only the proxy publishes ports, and the dev overlay binds the others to `127.0.0.1` (D-x, 6.1) | `test_committed_openapi_schema_is_current`; `integration/test_stack_smoke.py`; `infra/scripts/smoke.sh` |
| API10 | Unsafe Consumption of APIs | Every upstream answer is untrusted: LLM output is schema-validated (`complete_structured`), citations are checked against what was sent, and output is verified before release; fetched pages, PDFs and transcripts pass injection screening and quarantine; ClamAV failure means 503 and nothing stored (2.x, 3.x, 5.x) | `test_citing_a_source_that_was_not_given_falls_back`; `unit/test_llm_gateway.py` (`test_valid_json_is_parsed_and_schema_is_sent`, schema-echo recovery); `test_scanner_down_is_503_and_nothing_stored`; `integration/test_injection_pipeline.py` |

## OWASP Top 10 for LLM Applications (2025)

| # | Risk | Control (chunk) | Proof |
|---|---|---|---|
| LLM01 | Prompt Injection | Layered screening of every ingested derivative (invisible characters stripped, hidden text removed, a rule plus classifier detector, quarantine with human confirmation) (3.2); untrusted content sits inside a fresh random boundary per prompt; extraction intent raises risk and tightens the prompt (5.x); a note with an injection stops promotion | `integration/test_injection_pipeline.py` (whole file); `unit/test_injection_detection.py`; `test_prompt_messages_use_a_fresh_boundary_each_time`; `test_injection_in_a_note_stops_the_promotion`; `test_extraction_intent_adds_risk_and_tightens_the_prompt`; `frontend` quarantine component tests |
| LLM02 | Sensitive Information Disclosure | PII scrubbing (Presidio, spaCy `pl_core_news_lg`, Polish identifier recognisers) before anything leaves the notebook, with a second pass on LLM output; sensitive purposes are forced onto the local model and never fall back to the cloud (`force_local_llm=True`) (3.1, 3.3); with admin-configurable models (Stage 7) the local-only rule holds in four layers: service, Postgres triggers on routes/snapshots/providers, locality re-derived from the allowlists on load, and the gateway refusing any non-local route for a local-only call; provider API keys envelope-encrypted, write-only, never logged or audited; RLS keeps private notes private; GPS stripped by default (2.2) | `unit/test_scrubber.py`, `unit/test_pl_identifiers.py`; `test_pii_reconstructed_by_the_llm_is_scrubbed_again`; `test_force_local_with_only_a_cloud_route_is_refused`, `test_sensitive_purposes_stay_local_even_without_the_flag`, `test_local_failure_never_falls_back_to_cloud`; `unit/test_llm_config_store.py::test_a_tampered_route_to_the_cloud_is_refused`, `test_resolve_derives_locality_again_and_drops_disallowed_endpoints`; `integration/test_llm_config.py` (`test_local_only_purposes_refuse_cloud_models_in_service_and_database`, `test_a_tampered_snapshot_cannot_route_private_data_to_the_cloud`, `test_api_keys_are_encrypted_write_only_and_bound_to_their_row`); `test_notebook_qa_answers_from_own_notes_only`; `unit/test_sanitize.py`; E2E `3-uploads` |
| LLM03 | Supply Chain | Locked dependencies (`uv.lock`, `package-lock.json`); pip-audit, npm audit, trivy on every image, gitleaks; base images pinned by digest; models load from local directories only (`local_files_only`) (6.7, D-x) | `make audit`; CI job `security`; `injection_model.py` B615 annotation (see Accepted risks) |
| LLM04 | Data and Model Poisoning | Nothing enters the canonical corpus without expert review: a quorum of distinct experts with MFA, no self-review, optimistic version checks (4.1); injection screening upstream (3.2); disputed and degraded articles leave retrieval at once (4.x) | `integration/test_governance.py` (whole file); `test_disputed_leaves_retrieval_and_reapproval_restores_it`; `test_coauthor_revoke_cascade`; E2E `4-happy-path`, `5-exit-strategy` |
| LLM05 | Improper Output Handling | The UI renders model and wiki text through `SafeMarkdown` (react-markdown, no raw HTML, `rehype-sanitize`, URL allow-list), with the Trusted Types default policy (DOMPurify) behind it; the API returns validated structures, never raw model text where structure is expected (6.1, 6.3, 6.5) | `frontend/src/components/markdown/SafeMarkdown.test.tsx`; `frontend/src/lib/trusted-types.test.ts`; E2E `8-xss`, `7-headers` (an inline script injection is blocked and reported) |
| LLM06 | Excessive Agency | Models have no tools, no network and no write access: each call is a fixed purpose with a fixed output schema, routed by the gateway; every side effect (promotion, approval, revoke) is a human action through an authorised endpoint (3.x, 5.x). Changing which model serves a purpose is an audited admin action (draft, probe, activation, rollback) (7.1, 7.4) | `unit/test_llm_gateway.py` (`test_cloud_only_for_allowed_purposes`); `test_only_the_guard_and_its_wiring_import_rag_service` (only `AntiDistillationGuard` reaches retrieval); `test_draft_probe_activate_and_roll_back` |
| LLM07 | System Prompt Leakage | Prompts hold no secrets, keys or authorisation logic; authorisation lives in the API and RLS. Verbatim and n-gram output checks refuse dumps of the context, and the watermark attributes any leaked paraphrase (5.x) | `unit/test_output_verifier.py` (`test_json_dump_of_source_sentences_is_blocked`, `test_long_verbatim_span_inside_a_long_source_is_blocked`); `test_full_quote_is_refused_with_citations_and_audited`; `test_detector_singles_out_the_account_over_100_candidates` |
| LLM08 | Vector and Embedding Weaknesses | Every vector carries `author_id` and `consent_status`; retrieval filters on `ACTIVE` and re-checks Postgres (the source of truth) and repairs drift; the transactional outbox keeps Qdrant in step; private-notebook vectors are scoped to their owner; honeypot vectors never reach synthesis; raw chunks never leave the API (4.2, 4.3, 5.x) | `integration/test_vector_index.py`, `test_vector_outbox.py`; `test_block_suspended_in_postgres_is_dropped_and_repaired`; `test_suspend_then_resume_restores_retrieval`; `test_honeypots_never_reach_synthesis`; `test_notebook_qa_answers_from_own_notes_only`; smoke check "embedding model separates unrelated texts" |
| LLM09 | Misinformation | Answers come only from expert-approved canonical articles, with checked citations; a citation to a source the model was not given falls back to a refusal; with nothing above the score threshold there is no answer and no LLM call; superseded articles drop out at once (4.x, 5.x) | `test_grounded_answer_cites_the_article`, `test_citing_a_source_that_was_not_given_falls_back`, `test_below_threshold_is_no_answer`, `test_nothing_retrieved_means_no_llm_call`; `test_approve_with_supersede_removes_old_vectors_immediately`; E2E `4-happy-path` |
| LLM10 | Unbounded Consumption | Risk-scaled `top_k`, PoW per request at HIGH risk, lock at CRITICAL, coverage limits; bounded question and upload sizes; promotion quota; Celery job retries are bounded and a rejected request isn't retried (3.4, 5.x). The admin playground and probes never retrieve (import-linter contract), take bounded prompts and are rate limited per admin (7.5) | `test_elevated_risk_halves_top_k_silently`, `test_high_risk_requires_proof_of_work_for_each_request`, `test_critical_risk_locks_the_account`; `unit/test_guard.py`; `test_no_local_model_fails_after_retries`, `test_a_rejected_request_fails_without_retrying`; `test_playground_is_rate_limited`, `test_probes_are_rate_limited`; lint-imports "LLM playground and probes never retrieve content"; E2E `6-lockout` |

## Stage 7 additions (admin-configurable LLM models, US-01)

- **Cloud and creator consent.** With `LLM_ALLOW_CLOUD=true` the cooperative chat may send
  canonical wiki text to a cloud API. Enable it only after the cooperative's board confirms that
  creators' consent covers it. The activation dialog lists every purpose that would go to the cloud.
- **Tamper resistance.** A database write that bypasses the service cannot route private-data
  purposes to the cloud: triggers refuse it, the loading process re-derives locality from the
  allowlists, and the gateway refuses non-local routes for local-only calls
  (`llm_route_refused_total` must stay 0).
- **Probe reports** store pass/fail, timings, token counts and exception class names only; the
  playground's answers are returned to the admin and never stored (the audit log keeps a SHA-256
  of the prompt).

## Stage 8 additions (admin user management, US-02)

- **Least privilege for the API.** The `spoldzielnia-admin` service account holds
  `realm-management` `view-users`, `query-users` and `manage-users` only (`make keycloak-setup`
  removes anything else). It can read realm role names, which Keycloak needs for mapping, but
  cannot change the realm, clients or roles (integration test against the real realm). The API
  maps only `MEMBER`/`EXPERT`/`ADMIN`, resolved from the user's available roles, so a request
  can never grant `realm-admin`. The admin REST API is reachable only on the internal network;
  Caddy still refuses `/admin` on `auth.<domain>` from outside `KC_ADMIN_ALLOW`.
- **No lock-out of the organisation.** Self-demotion, self-blocking and demoting or blocking the
  last active admin are refused; admin rows are locked in id order before the target, so two
  admins acting on each other serialize (tested with both transactions open at once).
- **Temporary passwords** are 16 characters from an unambiguous alphabet, returned once with
  `Cache-Control: no-store`, rate limited (10 per admin per hour by default), and never written
  to logs, the audit log, Redis or the database. Prefer the e-mail link where the cooperative
  has SMTP.
- **Separate locks.** An admin block (`blocked_*`) and the shield's lock (`is_locked`) are
  separate columns; unblocking never lifts the shield's lock, and a Keycloak sync never lifts
  an admin block.
- **Drift.** Keycloak is written first. If the mirror write fails afterwards, the next login or a
  sync reconciles roles; a user disabled in the Keycloak console becomes blocked on sync.

## Accepted risks

Every entry is the reason a scanner is told to stay quiet. Re-check each entry when its trigger changes.

| Finding | Where | Why accepted | Re-check when |
|---|---|---|---|
| CVE-2016-3616, CVE-2020-13790 (libjpeg) | `.trivyignore`; worker images | pikepdf's manylinux wheel vendors RHEL 8's libjpeg-turbo `1.5.3-14.el8_10`. Trivy compares that RHEL build with Debian's fixed versions, which is a cross-ecosystem mismatch. Both CVEs are in cjpeg's input readers (`rdppm.c`, cjpeg), which neither libjpeg nor pikepdf uses, and the RHEL build postdates its fix for CVE-2020-13790 | pikepdf ships a new wheel |
| `braces` stack exhaustion (via `micromatch` → `fast-glob` → `@next/eslint-plugin-next` → `eslint-config-next`), 5 high findings | `frontend` **dev** dependencies | Lint-time only: absent from the runtime image (`npm audit --omit=dev` reports 0). The only fix is a breaking downgrade of `eslint-config-next`. `make audit` reports it for information and fails only on runtime findings | `eslint-config-next` updates |
| Bandit B104 (bind to `0.0.0.0`) | `core/settings.py` `api_host` | Binds inside the container; only Caddy on the internal network reaches it | the API runs outside a container |
| Bandit B108 (`/tmp` path) | `core/settings.py` `schedule_file` | Celery beat's schedule file sits on the container's tmpfs, owned by the non-root user | the beat service changes |
| Bandit B608 (SQL string building) | `audit/verify.py` | The interpolated piece is a module constant (`CANONICAL_ROW_SQL`); no input reaches it | that query is edited |
| Bandit B615 (Hugging Face download without a revision) | `llm/injection_model.py` | Loads from a local directory with `local_files_only=True`, so it never downloads | model loading changes |
| `pl-core-news-lg` not audited | `pip-audit` | A spaCy model wheel from GitHub releases, not on PyPI, so there is no advisory feed. It is data (weights), pinned by URL and hash in `uv.lock` | spaCy model upgrade |
| Coverage-ratio cut-off isn't shown end to end in E2E | E2E `6-lockout` | The dev corpus is tiny, so the absolute floor (gap G14) dominates the ratio. The ratio cut-off before 15% is proven on a 200-block corpus in `test_distillation_attack.py`; E2E proves the step-up → lock path and the admin view | — |
| Client-side deterrents (lazy render, no bulk export, wiki watermark) | 6.3 | Deterrence only; the server-side shield is the control | — |

## Environment-dependent checks

- **Embedding quality.** The smoke check "embedding model separates unrelated texts" and E2E `4-happy-path`
  depend on the configured embedding model. A degenerate model (unrelated texts with cosine ≥ 0.8)
  fails the smoke check, and global setup skips scenario 4 with a clear reason instead of letting it
  fail at random. `rag.score_threshold` is calibrated per model. Re-index (`make reindex`) after switching.
- **Promotion quota.** The promotion daily quota (20 per member) is real, so repeated test runs against the
  same dev stack can hit it until UTC midnight.
