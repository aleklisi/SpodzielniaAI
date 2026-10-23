#!/usr/bin/env bash
# Restore an installation from backup bundles (6.8): `make restore DATA=... [SECRETS=...]`.
# For a new server, or one whose volumes are gone; the runbook is docs/ops/DISASTER_RECOVERY.md.
#   1. decrypts the bundles with BACKUP_IDENTITY (the age private key, kept off the server);
#      the secrets bundle installs ./secrets and .env where absent, and stops on a file that
#      differs (FORCE_SECRETS=1 replaces them, keeping the old ones in ./secrets.replaced-<UTC>)
#   2. checks every file against the manifest's SHA-256
#   3. starts postgres and minio only; on empty volumes the init scripts create the roles and
#      databases from ./secrets. An installation with data stops the restore (FORCE=1 replaces it)
#   4. pg_restore --create --clean of `spoldzielnia` and `keycloak`; row counts must match the
#      manifest (STRICT=1 fails on a difference, else it is reported: the backup ran live)
#   5. migrations to the image's head, bootstrap (the bucket, server-side encryption)
#   6. objects mirrored back into the bucket
#   7. the whole stack, waited for until healthy
#   8. Qdrant rebuilt from Postgres (`reindex --yes`: text and atlas indexes)
#   9. the audit log's hash chain verified, its head compared with the manifest's
# Then run `make smoke` (deploy.sh does on servers).
#   COMPOSE  compose command (default: production file set; lib-ops.sh). VERSION defaults to
#            the version the manifest recorded.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=infra/scripts/lib-ops.sh
source infra/scripts/lib-ops.sh

DATA="${DATA:-${1:-}}" SECRETS="${SECRETS:-${2:-}}"
IDENTITY="$(env_get BACKUP_IDENTITY)"
[[ -s "$DATA" ]] || die "usage: make restore DATA=<…data.tar.age> [SECRETS=<…secrets.tar.age>] BACKUP_IDENTITY=<key>"
[[ -s "$IDENTITY" ]] || die "BACKUP_IDENTITY must name the age identity file (the private key)"
command -v age >/dev/null || die "age is not installed (apt install age / brew install age)"
umask 077
make_staging "$(dirname "$DATA")/.staging"

step "1. Decrypt"
if [[ -n "$SECRETS" ]]; then
  mkdir -p "$STAGING/secrets-bundle"
  # -p: the modes as saved (secret files 0644 for the containers, CA keys 0600), not the umask.
  age -d -i "$IDENTITY" "$SECRETS" | tar -C "$STAGING/secrets-bundle" -xpf -
  differing=()
  for file in "$STAGING"/secrets-bundle/secrets/*; do
    name="secrets/$(basename "$file")"
    [[ -f "$name" ]] && ! cmp -s "$file" "$name" && differing+=("$name")
  done
  [[ -f .env ]] && ! cmp -s "$STAGING/secrets-bundle/.env" .env && differing+=(.env)
  if ((${#differing[@]})) && [[ "${FORCE_SECRETS:-0}" != "1" ]]; then
    die "these differ from the bundle: ${differing[*]}. FORCE_SECRETS=1 replaces them (the old ones are kept)"
  fi
  if ((${#differing[@]})); then
    keep="secrets.replaced-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$keep" && cp -p secrets/* "$keep/" 2>/dev/null || true
    [[ -f .env ]] && cp -p .env "$keep/.env"
    warn "replaced ${differing[*]} (previous copies in $keep)"
  fi
  mkdir -p secrets && chmod 700 secrets
  cp -Rp "$STAGING"/secrets-bundle/secrets/. secrets/
  [[ -f .env ]] || cp -p "$STAGING/secrets-bundle/.env" .env
  echo "  secrets and .env in place"
fi
[[ -s secrets/kek ]] || die "./secrets has no kek: pass SECRETS=<…secrets.tar.age>"
mkdir -p "$STAGING/data"
age -d -i "$IDENTITY" "$DATA" | tar -C "$STAGING/data" -xf -
MANIFEST="$STAGING/data/manifest.json"
manifest() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v=v[k]
print(v)' "$MANIFEST" "$1"; }
export VERSION="${VERSION:-$(manifest version)}"
echo "  backup of $(manifest created_at): version $VERSION, schema $(manifest schema_revision)"

step "2. Checksums"
python3 - "$STAGING/data" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = json.loads((root / "manifest.json").read_text())["files"]
bad = [name for name, digest in files.items()
       if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
extra = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
         and str(p.relative_to(root)) not in files and p.name != "manifest.json"]
if bad or extra:
    sys.exit(f"bundle does not match its manifest: {len(bad)} changed/missing, {len(extra)} unexpected")
print(f"  {len(files)} files verified")
PY

step "3. Databases and object storage"
"${COMPOSE[@]}" up -d --wait postgres minio
tables="$(pg psql -X -At -d spoldzielnia -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
if [[ "$tables" != "0" ]]; then
  [[ "${FORCE:-0}" == "1" ]] || die "the database already holds an installation ($tables tables). FORCE=1 replaces it"
  warn "FORCE=1: replacing the running installation"
fi
# Nothing may hold a connection while the databases are dropped and recreated.
running="$("${COMPOSE[@]}" ps --services --status running | grep -vx -e postgres -e minio || true)"
if [[ -n "$running" ]]; then
  # shellcheck disable=SC2086 # one service name per word
  "${COMPOSE[@]}" stop $running
fi

step "4. pg_restore"
for db in spoldzielnia keycloak; do
  # --create --clean: drops the (empty or replaced) database and recreates it with its owner and
  # grants as saved; the roles come from the init scripts and ./secrets.
  pg pg_restore --create --clean --if-exists --exit-on-error -d postgres < "$STAGING/data/postgres/$db.dump"
  echo "  $db restored"
done
COUNT_SQL="SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text
           FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
mismatch=0
for db in spoldzielnia keycloak; do
  pg psql -X -At -F $'\t' -d "$db" -c "$COUNT_SQL" > "$STAGING/$db.counts.tsv"
  if ! diff -u "$STAGING/data/postgres/$db.counts.tsv" "$STAGING/$db.counts.tsv" > "$STAGING/$db.diff"; then
    mismatch=1
    warn "$db row counts differ from the manifest:"; sed 's/^/    /' "$STAGING/$db.diff" >&2
  fi
done
if ((mismatch)) && [[ "${STRICT:-0}" == "1" ]]; then die "row counts differ (STRICT=1)"; fi
((mismatch)) || echo "  row counts match the manifest"

step "5. Migrations and bootstrap"
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" run --rm bootstrap

step "6. Objects"
"${COMPOSE[@]}" --profile ops run --rm --no-deps --quiet-pull --user "$(id -u):$(id -g)" \
  -v "$STAGING/data:/backup:ro" -e MODE=load backup >/dev/null
restored="$("${COMPOSE[@]}" --profile ops run --rm --no-deps -e MODE=list backup 2>/dev/null | grep -c '"type":"file"' || true)"
expected="$(manifest objects.count)"
[[ "$restored" == "$expected" ]] || die "object storage holds $restored objects, the backup $expected"
echo "  $restored objects"

step "7. Stack"
"${COMPOSE[@]}" up -d --wait --wait-timeout "${WAIT_TIMEOUT:-600}"

step "8. Search indexes (Qdrant) from Postgres"
if out="$("${COMPOSE[@]}" run --rm worker-core reindex --yes 2>&1)"; then
  grep -E 'reindex.done|atlas' <<< "$out" | tail -2 | sed 's/^/  /'
elif grep -q model_not_configured <<< "$out"; then
  warn "no embedding model configured: the index is built once an admin routes one (10.2)"
else
  echo "$out" >&2
  die "reindex failed"
fi

step "9. Audit log"
"${COMPOSE[@]}" run --rm --no-deps api audit-verify
head_now="$(pg psql -X -At -d spoldzielnia -c "SELECT id || ' ' || encode(hash, 'hex') FROM security_audit_log WHERE id = $(manifest audit_head | cut -d' ' -f1)")"
[[ "$head_now" == "$(manifest audit_head)" ]] || die "the audit entry the backup ended with is not the same: $head_now"
echo "  chain intact through the backup's last entry #$(manifest audit_head | cut -d' ' -f1)"

echo
echo "restored $(manifest created_at) (version $VERSION). Next: make smoke"
