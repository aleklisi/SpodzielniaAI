# Sourced by the operations scripts (6.8: deploy, backup, restore, create-admin).
#   COMPOSE  compose command; default: the production file set (compose.yaml + compose.prod.yaml,
#            + compose.gpu.yaml with GPU=1). The dev stack passes its own (`make backup` in dev).
if [[ -n "${COMPOSE:-}" ]]; then
  read -r -a COMPOSE <<< "$COMPOSE"
else
  COMPOSE=(docker compose -f compose.yaml -f compose.prod.yaml)
  [[ "${GPU:-0}" == "1" ]] && COMPOSE+=(-f compose.gpu.yaml)
fi

step() { echo; echo "== $*"; }
warn() { echo "  ! $*" >&2; }
die() { echo "error: $*" >&2; exit 1; }

# A value from .env without sourcing it (the file is data, not a script): the last `KEY=`
# line, surrounding quotes removed. Variables already in the environment win, as in compose.
env_get() {
  local key="$1" value
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return; fi
  value="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2-)" || true
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

# The settings a production deploy cannot run without, exported for compose and smoke.sh.
require_prod_env() {
  [[ -f .env ]] || die ".env is missing: copy .env.prod.example to .env and fill it in"
  REGISTRY="$(env_get REGISTRY)" SITE_DOMAIN="$(env_get SITE_DOMAIN)" ACME_EMAIL="$(env_get ACME_EMAIL)"
  [[ -n "$REGISTRY" ]] || die "REGISTRY is not set in .env (e.g. ghcr.io/<owner>/spoldzielnia)"
  [[ -n "$SITE_DOMAIN" && "$SITE_DOMAIN" != *localhost ]] || die "SITE_DOMAIN must be the public domain"
  [[ "$ACME_EMAIL" == *@* ]] || die "ACME_EMAIL must be an e-mail address (Let's Encrypt account)"
  [[ "$(env_get MFA_REQUIRED)" != "false" ]] || die "MFA_REQUIRED=false is a dev setting: remove it from .env"
  local environment; environment="$(env_get SPOLDZIELNIA_ENVIRONMENT)"
  [[ -z "$environment" || "$environment" == "prod" ]] || die "SPOLDZIELNIA_ENVIRONMENT=$environment in .env"
  export REGISTRY SITE_DOMAIN ACME_EMAIL
}

# The Alembic head an api image ships (no network, no secrets needed).
schema_head() {
  docker run --rm --network none --entrypoint python "$REGISTRY/api:$1" -c \
    'from spoldzielnia_ai.db.migrate import head_revision; print(head_revision())'
}

# sha256sum on Linux, shasum on macOS; same output format.
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

# Postgres as the superuser over the container's local socket (peer authentication): no
# password leaves the container.
pg() { "${COMPOSE[@]}" exec -T -u postgres postgres "$@"; }

# A private staging directory, removed on exit (it holds plaintext dumps while they are packed).
make_staging() {
  local base="${1:-${TMPDIR:-/tmp}}"
  mkdir -p "$base"
  STAGING="$(mktemp -d "$base/spoldzielnia-ops.XXXXXX")"
  chmod 700 "$STAGING"
  trap 'rm -rf "$STAGING"' EXIT
}
