#!/usr/bin/env bash
# Deploy or upgrade the server to an image version (6.8): `make deploy VERSION=v1.2.3`.
# Runs on the server, from a checkout of the same tag (compose files, Caddyfile, init scripts).
#   1. checks .env (production settings, MFA on) and ./secrets
#   2. pulls the four images of VERSION (OFFLINE=1: they were `docker load`ed)
#   3. downloads the offline models into the `models` volume if it does not match its manifest
#   4. backs up the running installation first (BACKUP=0 skips; not on the first deploy)
#   5. applies migrations (one-shot `migrate`, owner role; expand/contract, DEPLOYMENT_DOCKER §9)
#   6. starts or restarts every service and waits until healthy
#   7. runs the smoke test against SITE_DOMAIN
#   8. records VERSION in .deploy/ (current, previous, history)
# Rollback (`make rollback`): back to .deploy/previous, allowed only when both versions ship the
# same schema head (the API is ready only at its own head); otherwise restore the backup from
# step 4 (docs/ops/DISASTER_RECOVERY.md) or roll forward.
#   COMPOSE   compose command (default: base + prod files, + compose.gpu.yaml with GPU=1)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=infra/scripts/lib-ops.sh
source infra/scripts/lib-ops.sh

VERSION="${VERSION:-${1:-}}"
[[ -n "$VERSION" ]] || die "usage: make deploy VERSION=vX.Y.Z"
export VERSION
STATE="$ROOT/.deploy"
mkdir -p "$STATE"

step "1. Configuration"
require_prod_env
[[ -s secrets/kek ]] || die "./secrets is empty: run 'make secrets' (new server) or restore the secrets bundle"
for file in compose.yaml compose.prod.yaml infra/keycloak/realm-spoldzielnia.prod.json; do
  [[ -f "$file" ]] || die "$file is missing: is this a checkout of $VERSION?"
done
checkout="$(git describe --tags --exact-match 2>/dev/null || true)"
if [[ -n "$checkout" && "$checkout" != "$VERSION" ]]; then
  warn "the checkout is $checkout, deploying images $VERSION: compose files may not match"
fi
"${COMPOSE[@]}" config -q
previous="$(cat "$STATE/current" 2>/dev/null || true)"
echo "  deploying $VERSION (running: ${previous:-nothing})"

step "2. Images"
if [[ "${OFFLINE:-0}" == "1" ]]; then
  for image in api worker-core worker-media frontend; do
    docker image inspect "$REGISTRY/$image:$VERSION" >/dev/null || die "$REGISTRY/$image:$VERSION not loaded"
  done
else
  "${COMPOSE[@]}" pull --quiet --ignore-buildable
fi

if [[ "${ROLLBACK:-0}" == "1" && -n "$previous" ]]; then
  now="$(schema_head "$previous")" target="$(schema_head "$VERSION")"
  [[ "$now" == "$target" ]] || die "rollback refused: $previous ships schema $now, $VERSION ships $target.
  Restore the backup taken before $previous was deployed, or roll forward (docs/ops/DISASTER_RECOVERY.md)."
fi

step "3. Offline models"
if "${COMPOSE[@]}" run --rm --no-deps worker-media models verify --root /models >/dev/null 2>&1; then
  echo "  models volume matches its manifest"
else
  echo "  downloading (needs internet on the egress network)"
  "${COMPOSE[@]}" --profile tools run --rm models-init
fi

step "4. Backup before the change"
if [[ -z "$previous" ]]; then
  echo "  first deploy: nothing to back up"
elif [[ "${BACKUP:-1}" == "0" ]]; then
  warn "skipped (BACKUP=0)"
else
  COMPOSE="${COMPOSE[*]}" infra/scripts/backup.sh
fi

step "5. Migrations"
if [[ "${ROLLBACK:-0}" == "1" ]]; then
  echo "  rollback to the same schema head: nothing to migrate"
else
  "${COMPOSE[@]}" run --rm migrate
fi

step "6. Services"
"${COMPOSE[@]}" up -d --wait --wait-timeout "${WAIT_TIMEOUT:-600}" --remove-orphans

step "7. Smoke test"
COMPOSE="${COMPOSE[*]}" SITE_DOMAIN="$SITE_DOMAIN" CADDY_TLS="$ACME_EMAIL" infra/scripts/smoke.sh

step "8. Record"
if [[ -n "$previous" && "$previous" != "$VERSION" ]]; then
  echo "$previous" > "$STATE/previous"
fi
echo "$VERSION" > "$STATE/current"
kind=deploy; [[ "${ROLLBACK:-0}" == "1" ]] && kind=rollback
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$VERSION" "$kind" >> "$STATE/history"
echo "deployed $VERSION"
