# Contributing to Spółdzielnia AI

> **PL:** Dziękujemy za chęć pomocy! Zgłoszenia, dyskusje i pull requesty mogą być po polsku
> albo po angielsku. Szczegółowe zasady pracy są w [`AGENTS.md`](AGENTS.md) (po polsku).

Thank you for your interest! Issues, discussions and pull requests are welcome in Polish or
English. The detailed rules for working on the code are in [`AGENTS.md`](AGENTS.md) (in Polish);
this file is the short version.

## Before you start

- **Security problems** are never reported in public — follow [`SECURITY.md`](SECURITY.md).
- **Bugs:** open an issue with steps to reproduce, what you expected and what happened, and the
  commit you are on.
- **New features:** open an issue first and describe the need as a user story ("As a … I want …
  so that …"). Larger changes are planned before they are built (see *New features* below), so
  agreeing on the plan early saves you work.

## Setting up

You need Docker, [`uv`](https://docs.astral.sh/uv/) and Node.js. The
[README](README.md#quick-start) has the quick start; in short:

```bash
make up        # build and start the whole stack (generates dev secrets into ./secrets)
make migrate   # apply database migrations
```

Development users and their passwords are described in `AGENTS.md`, section 8.

## The rules that cannot be broken

Every change must keep the architectural rules in `AGENTS.md`, section 3:

1. **Isolation:** private notes are read only through a session with Row-Level Security set.
2. **Provenance:** every text block and its vector carry `author_id` and `consent_status`.
3. **Anti-distillation:** the API never returns raw stored chunks; only a synthesis that passed
   `AntiDistillationGuard`. Any new way knowledge leaves the system passes the same checks and
   fails closed.
4. **Private data stays with approved providers:** no fallback to another model provider.

A pull request that weakens one of these will not be merged, however useful the feature is.

## Making a change

1. Fork the repository and create a branch from `main` (e.g. `fix/upload-size`, `feat/export`).
2. Write the code **and the tests**: unit and integration tests for the backend; Vitest and,
   for visible changes, Playwright tests for the frontend.
3. Follow the conventions in `AGENTS.md`, sections 5–7. The most common review comments:
   - API errors are machine codes; texts for people live in the frontend catalogues;
   - every UI text goes into **both** `frontend/src/messages/pl/` and `frontend/src/messages/en/`,
     never into the code;
   - no inline `style="…"` (the CSP forbids it); lay pages out with container queries;
   - new migrations and tables are registered in `tests/unit/test_db_models.py`;
   - dependencies are pinned to an exact version at least two weeks old, with a reason.
4. Run the checks — all must pass:

   ```bash
   # backend, from the repository root
   uv run ruff check --fix . && uv run ruff format .
   uv run pyright
   uv run pytest

   # frontend, from frontend/ (after an API change, first: npm run gen:api)
   npm run lint && npm run typecheck && npx vitest run && npm run build

   # end to end, on the running stack
   make e2e
   ```

   If a test fails for a reason outside your change (for example E2E scenario 4, which depends
   on the configured models), say so in the pull request and show the evidence.

5. Commit with [Conventional Commits](https://www.conventionalcommits.org/) messages, as in the
   history: `feat(stage-14): …`, `fix: …`, `docs: …`, `test: …`.
6. Open a pull request against `main`: what changes and why, how you tested it, and screenshots
   for UI changes (desktop and phone width).

## New features

Larger features follow the same two steps as the existing stages (`AGENTS.md`, section 4.1):

1. **Plan** — a file `docs/implementation/NN_NAME.md` with the decisions, acceptance criteria,
   design rules, implementation chunks and risks, plus entries in `PLAN.md` and
   `docs/USER_STORIES_LOG.md`. This can be its own pull request.
2. **Implementation** — the code, tests, the *Implementation*, *Changes against the plan* and
   *Tests* sections of the stage file, and a section in `docs/SECURITY_REVIEW.md`.

## Translations

Polish is the default language. Improvements to the English texts in
`frontend/src/messages/en/` are very welcome; keep the terms in the glossary
`frontend/src/messages/README.md` consistent. The test `src/i18n/messages.test.ts` checks that
both languages have the same keys and arguments.

## AI assistants

Contributions written with AI coding assistants are welcome. `AGENTS.md` is written for them as
well as for people — point your assistant at it. You remain responsible for the change: review
it, run the checks, and mark AI co-authorship with a `Co-Authored-By:` line if your tool adds one.

## Licence

By contributing, you agree that your contribution is licensed under the [MIT License](LICENSE),
like the rest of the project.
