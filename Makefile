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

# Optional built-in models (10.7): `make models WHISPER=1 DOCLING_OCR=1 SIGLIP=1`. The prompt-
# injection classifier is always downloaded. WHISPER=1 takes WHISPER_MODEL (default: small).
WHISPER ?=
DOCLING_OCR ?=
SIGLIP ?=
WHISPER_MODEL ?= small

models: ## Download offline ML models into the `models` volume (internet; see WHISPER/DOCLING_OCR/SIGLIP)
	MODELS_WHISPER="$(if $(filter 1 true yes,$(WHISPER)),$(WHISPER_MODEL),)" \
	MODELS_DOCLING="$(if $(filter 1 true yes,$(DOCLING_OCR)),true,false)" \
	MODELS_IMAGE_EMBED="$(if $(filter 1 true yes,$(SIGLIP)),true,false)" \
	$(COMPOSE) --profile tools run --rm --build models-init
	@echo "Turn on what you installed in .env: LLM_BUILTIN_TRANSCRIPTION / _OCR / _IMAGE_EMBED=true"

models-host: ## Models for host-side `ml` tests (.models: whisper tiny + Docling)
	uv run spoldzielnia models download --root .models --whisper tiny --docling true

reindex: ## Re-embed the text index into a new generation from Postgres, then swap (10.3)
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

# --- Production server (6.8; docs/ops/DISASTER_RECOVERY.md) ------------------
# These run on the server with compose.yaml + compose.prod.yaml (+ compose.gpu.yaml: GPU=1);
# COMPOSE="..." points backup/restore at another stack.
.PHONY: deploy rollback backup restore dr-rehearsal create-admin prod-realm
deploy: ## Deploy/upgrade the server: make deploy VERSION=v1.2.3 (pull, backup, migrate, up, smoke)
	@VERSION="$(VERSION)" infra/scripts/deploy.sh

rollback: ## Back to the previous version (same schema head only; else restore a backup)
	@test -s .deploy/previous || { echo "no previous version in .deploy/previous"; exit 2; }
	@ROLLBACK=1 VERSION="$$(cat .deploy/previous)" infra/scripts/deploy.sh

backup: ## Encrypted backup (age): Postgres, objects, manifest; secrets in a separate bundle
	@infra/scripts/backup.sh

restore: ## Restore: make restore DATA=<..data.tar.age> SECRETS=<..secrets.tar.age> BACKUP_IDENTITY=<key>
	@DATA="$(DATA)" SECRETS="$(SECRETS)" infra/scripts/restore.sh

dr-rehearsal: ## Back up the running dev stack, restore it into a bare copy, verify, clean up
	@SOURCE_COMPOSE="$(COMPOSE)" infra/scripts/dr-rehearsal.sh

create-admin: ## First administrator of a new server: make create-admin EMAIL=... NAME="First Last"
	@EMAIL="$(EMAIL)" NAME="$(NAME)" infra/scripts/create-admin.sh

prod-realm: ## Regenerate infra/keycloak/realm-spoldzielnia.prod.json from the dev realm export
	@python3 infra/scripts/gen-prod-realm.py

# --- Quality gates (host, via uv) --------------------------------------------
.PHONY: check test test-int
check: ## ruff + pyright + unit tests
	uv run ruff check --fix . && uv run ruff format . && uv run pyright && uv run pytest -m "not integration"

test: ## Unit tests
	uv run pytest -m "not integration and not ml and not e2e"

test-int: ## Integration tests (needs Docker / running stack)
	uv run pytest -m integration
