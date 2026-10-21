SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE ?= docker compose -f compose.yaml -f compose.dev.yaml

.PHONY: help
help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

# --- Stack -------------------------------------------------------------------
.PHONY: secrets build up watch down reset logs ps smoke audit e2e trust-cert mfa-off mfa-on keycloak-setup migrate revision models models-host reindex
secrets: ## Generate Docker secrets + dev TLS certs into ./secrets (idempotent)
	@infra/scripts/init-secrets.sh

build: ## Build backend image(s) with buildx bake
	docker buildx bake --load

up: secrets ## Build and start the dev stack, wait until healthy
	$(COMPOSE) up -d --build --wait --wait-timeout 420

watch: ## Rebuild/restart the API on source changes
	$(COMPOSE) watch

migrate: ## Apply DB migrations in the stack (owner role, one-shot container)
	$(COMPOSE) run --rm --build migrate

# Autogenerates against the dev DB (loopback port 5432) as the owner role; review the result.
revision: ## New migration: make revision REV=0002_rls MSG="row level security"
	@test -n "$(REV)" -a -n "$(MSG)" || { echo "usage: make revision REV=0002_name MSG='...'"; exit 2; }
	SPOLDZIELNIA_DB__HOST=127.0.0.1 SPOLDZIELNIA_DB__OWNER_PASSWORD="$$(cat secrets/pg_owner_password)" \
	  uv run alembic revision --autogenerate --rev-id "$(REV)" -m "$(MSG)"
	uv run ruff format src/spoldzielnia_ai/db/migrations/versions

models: ## Download the offline ML models into the `models` volume (internet, one-shot)
	$(COMPOSE) --profile tools run --rm --build models-init

models-host: ## Models for host-side `ml` tests (.models: whisper tiny + Docling)
	uv run spoldzielnia models download --root .models --whisper tiny

reindex: ## Rebuild both Qdrant collections from Postgres (e.g. after changing LLM_EMBED_DIM)
	$(COMPOSE) run --rm worker-core reindex --yes

audit: ## Security scanning: pip-audit, bandit, npm audit, gitleaks, trivy (after `make build`)
	@infra/scripts/audit.sh

e2e: ## Playwright E2E against the running dev stack (`make up` first)
	cd frontend && npx playwright test

smoke: ## Stack smoke test (HTTPS, IdP, hardening, ports, isolation)
	@COMPOSE="$(COMPOSE)" infra/scripts/smoke.sh

trust-cert: ## Trust Caddy's local root CA in the macOS login keychain (asks for your password)
	$(COMPOSE) cp proxy:/data/caddy/pki/authorities/local/root.crt ./secrets/tls/caddy-root.crt
	security add-trusted-cert -r trustRoot -k "$$HOME/Library/Keychains/login.keychain-db" ./secrets/tls/caddy-root.crt

keycloak-setup: ## Keycloak client + roles for user management (idempotent, existing realms)
	@COMPOSE="$(COMPOSE)" infra/scripts/keycloak-setup.sh

mfa-off: ## Dev only: log in without TOTP; the API stops requiring MFA (`make mfa-on` reverts)
	@COMPOSE="$(COMPOSE)" infra/scripts/dev-mfa.sh off

mfa-on: ## Restore TOTP at login and MFA for experts/admins
	@COMPOSE="$(COMPOSE)" infra/scripts/dev-mfa.sh on

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) --profile tools down

reset: ## Stop the stack and DELETE all volumes (databases, objects, vectors)
	$(COMPOSE) --profile tools down --volumes --remove-orphans

logs: ## Follow logs (S=<service> to filter)
	$(COMPOSE) logs -f --tail=200 $(S)

ps: ## Service status
	$(COMPOSE) ps

# --- Quality gates (host, via uv) --------------------------------------------
.PHONY: check test test-int
check: ## ruff + pyright + unit tests
	uv run ruff check --fix . && uv run ruff format . && uv run pyright && uv run pytest -m "not integration"

test: ## Unit tests
	uv run pytest -m "not integration and not ml and not e2e"

test-int: ## Integration tests (needs Docker / running stack)
	uv run pytest -m integration
