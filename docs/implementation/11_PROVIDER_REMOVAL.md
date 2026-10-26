# Stage 11 — Removing a model provider (US-05)

> **Story ([US-05](../USER_STORIES_LOG.md#us-05)):** as an administrator I want to remove an LLM
> provider I no longer use, so the catalog only shows configurations that matter and no unused
> API keys stay stored.

## Acceptance criteria

```gherkin
Background: admin1 is logged in and opens /admin/llm → "Dostawcy"

Scenario: remove an unused provider
  Given the list shows a provider that no configuration uses
  When admin1 clicks "Usuń" next to it and confirms
  Then the list refreshes without it, together with its models
  And a message says "Dostawca „<name>” został usunięty."
  And the audit log records LLM_PROVIDER_DELETED (name, URL, mode, model count; never the key)

Scenario: a provider in use cannot be removed
  Given the provider serves the active revision, or a serving/building index generation
  Then its "Usuń" button is disabled, with the reason next to it
  And DELETE answers 409 provider_in_use / provider_in_index

Scenario: a provider in a draft
  Given the open draft routes a purpose to one of its models
  Then removal is refused with 409 provider_in_draft, naming the draft

Scenario: a provider from an older revision
  Given an archived revision used the provider
  When admin1 removes it
  Then it leaves the list; its key is erased and its clearance revoked
  And History still shows the old revision by name
  But rolling back to that revision is refused with provider_removed
  And its name can be used for a new provider
```

## Design (rule R1)

ACTIVE and ARCHIVED revisions are immutable and their routes reference models with `ON DELETE
RESTRICT` (migration 0012): the history and rollback depend on them. So "remove" has two forms,
chosen by the server; the admin sees one action:

| Provider | Removal |
|---|---|
| No activated revision ever used it | **Deleted**: the provider row, its models and their probe reports |
| A past revision used it (routes, or the snapshot of a revision seeded from `.env`) | **Archived** (`llm_provider.deleted_at`): API key erased, clearance revoked, provider and models disabled, hidden from every list and lookup (404), never routed to again |

Refused (409) while something running needs it, checked in this order:

1. `provider_in_use`: the ACTIVE revision's snapshot names it;
2. `provider_in_index`: a SERVING or BUILDING index generation (text or atlas) was built by one
   of its models (`index_generation.deployment.provider_id`);
3. `provider_in_draft`: an open draft routes to one of its models (`detail.draft` = its number).

No MFA step-up: removing only narrows where data can go (like "Wyłącz"). The confirmation dialog
says what will happen (deleted or archived, models count, key erased, clearance revoked).

## Defence in depth (migration `0026_provider_removal`)

- `uq_llm_provider_name` becomes a unique index `WHERE deleted_at IS NULL` (names are reusable).
- `llm_provider_removed_guard`: `deleted_at` is set once and never cleared or changed; an
  archived provider cannot be cleared for private data again.
- `llm_routing_removed_guard`: no route may point to a model of an archived provider.
- `llm_revision_removed_guard`: no revision becomes ACTIVE with a snapshot naming one.
- Loading a configuration (`config_store.resolve`) and an index generation's model
  (`vector.generations.resolve_embed`) treat an archived provider as missing: the purpose becomes
  unavailable, never served by another provider (P5).

## Implementation

| Area | Where |
|---|---|
| Migration | `db/migrations/versions/0026_provider_removal.py` |
| Service | `LlmConfigService.removals()`, `delete_provider()`; `_provider()`/`_model()` hide archived rows; `_check_route` refuses `provider_removed` (rollback) |
| Audit | `EventType.LLM_PROVIDER_DELETED` `{name, kind, base_url, mode, models, was_cleared}` |
| API | `DELETE /admin/llm/providers/{id}` → 204 / 404 / 409; `ProviderOut.removal_blocked`, `removal_archives`, `removal_draft` |
| UI | `Catalog.tsx`: „Usuń” per provider (disabled with the reason), `RemoveDialog`; `useDeleteProvider` refreshes the console |

## Tests

- `tests/integration/test_provider_removal.py`: delete with models and audit; each 409 path;
  archive (key, clearance, models, hidden, 404 for every action, rollback refused, name reuse);
  resolution drops archived providers; the 0026 triggers; the API (authorization, CSRF, refreshed
  list, 404 after removal, 409 for the provider in use).
- Vitest `llm.test.tsx` „removal (11.1)”: remove and refreshed list, disabled button with the
  reason, archive explanation and a refusal message.
- E2E `13-provider-removal.spec.ts`: admin1 adds a provider through the console, sees the
  provider in use with a disabled „Usuń”, removes the new one, and the list (also after a reload)
  no longer shows it.
