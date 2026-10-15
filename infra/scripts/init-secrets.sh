#!/usr/bin/env bash
# Generate Docker secrets into ./secrets (gitignored). Idempotent: existing files are kept,
# so re-running never rotates a credential that a database volume already depends on.
#
# Permissions: the directory is 0700 (only the owner can reach the files on the host).
# Files are 0644 because compose bind-mounts file secrets with host permissions and the
# consuming containers run as different non-root uids (postgres 999, keycloak 1000, …).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIR="${SECRETS_DIR:-$ROOT/secrets}"
mkdir -p "$DIR"
chmod 700 "$DIR"

rand_token() { openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-"${1:-40}"; }
rand_key_b64() { openssl rand -base64 32 | tr -d '\n'; }

write() {
  local name="$1" value="$2"
  if [[ -s "$DIR/$name" ]]; then
    echo "  keep     $name"
  else
    printf '%s' "$value" > "$DIR/$name"
    echo "  created  $name"
  fi
  chmod 644 "$DIR/$name"
}

echo "Secrets in $DIR"
# PostgreSQL
write pg_superuser_password "$(rand_token)"
write pg_owner_password     "$(rand_token)"
write pg_app_password       "$(rand_token)"
write pg_keycloak_password  "$(rand_token)"
# Redis / Qdrant / MinIO
write redis_password        "$(rand_token)"
write qdrant_api_key        "$(rand_token 48)"
write minio_root_password   "$(rand_token)"
write minio_kms_key         "spoldzielnia-sse:$(rand_key_b64)"
# Identity
write oidc_client_secret      "$(rand_token 48)"
write keycloak_admin_password "$(rand_token)"
# Application keys (used from Stage 2 on)
write session_secret        "$(rand_key_b64)"
write kek                   "$(rand_key_b64)"
write watermark_key         "$(rand_key_b64)"
write honeypot_key          "$(rand_key_b64)"
# Dev-realm test users only (never imported in prod)
write dev_user_password     "$(rand_token 20)"
write dev_totp_seed         "$(rand_token 20)"

"$ROOT/infra/scripts/gen-dev-certs.sh"
