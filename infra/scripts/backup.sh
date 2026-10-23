#!/usr/bin/env bash
# Encrypted backup of a running installation (6.8): `make backup`, nightly from a systemd timer
# on the server (docs/ops/DISASTER_RECOVERY.md). Writes two age-encrypted bundles:
#   spoldzielnia-<UTC>-<version>.data.tar.age
#       pg_dump -Fc of `spoldzielnia` and `keycloak`, the object bucket (mc mirror over the S3
#       API), manifest.json (checksums, row counts, schema revision, last audit entry)
#   spoldzielnia-<UTC>-<version>.secrets.tar.age
#       ./secrets (the KEK: notebooks are unreadable without it) and .env, to its own
#       recipients: keep that key apart
# Not backed up, by design: Qdrant (derived from Postgres: `reindex` after a restore), Redis
# (sessions, queues and shield windows are short-lived), the offline models (`make models`).
# Postgres is dumped before the objects: an object deleted in between (a revoke) is missing from
# the bundle; one added in between is an orphan. Run it at a quiet hour.
#   BACKUP_DIR                 where the bundles go (default ./backups)
#   BACKUP_RECIPIENTS          age recipients file (public keys) for the data bundle (required)
#   BACKUP_SECRETS_RECIPIENTS  recipients for the secrets bundle (default: BACKUP_RECIPIENTS)
#   BACKUP_KEEP                bundles of each kind kept in BACKUP_DIR (default 14, 0 keeps all)
#   COMPOSE                    compose command (default: production file set; lib-ops.sh)
# Off-site copies are the operator's (rclone/rsync of BACKUP_DIR after this script).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=infra/scripts/lib-ops.sh
source infra/scripts/lib-ops.sh

command -v age >/dev/null || die "age is not installed (apt install age / brew install age)"
DIR="$(env_get BACKUP_DIR)"; DIR="${DIR:-$ROOT/backups}"
RECIPIENTS="$(env_get BACKUP_RECIPIENTS)"
SECRETS_RECIPIENTS="$(env_get BACKUP_SECRETS_RECIPIENTS)"; SECRETS_RECIPIENTS="${SECRETS_RECIPIENTS:-$RECIPIENTS}"
KEEP="$(env_get BACKUP_KEEP)"; KEEP="${KEEP:-14}"
[[ -s "$RECIPIENTS" ]] || die "BACKUP_RECIPIENTS must name an age recipients file (age-keygen -y identity.txt)"
[[ -s "$SECRETS_RECIPIENTS" ]] || die "BACKUP_SECRETS_RECIPIENTS: no such recipients file"
umask 077
mkdir -p "$DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
VERSION_NOW="$(cat .deploy/current 2>/dev/null || echo "${VERSION:-dev}")"
# The version in the name tells which tag to check out before restoring it.
NAME="spoldzielnia-$STAMP-$VERSION_NOW"
make_staging "$DIR/.staging"
mkdir -p "$STAGING/postgres" "$STAGING/minio"

step "Postgres (pg_dump -Fc)"
for db in spoldzielnia keycloak; do
  pg pg_dump -Fc -d "$db" > "$STAGING/postgres/$db.dump"
  echo "  $db: $(du -h "$STAGING/postgres/$db.dump" | cut -f1)"
done
# Exact row counts (the superuser bypasses RLS) and the audit chain's last entry, so a restore
# can prove it brought back what was saved.
COUNT_SQL="SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text
           FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
for db in spoldzielnia keycloak; do
  pg psql -X -At -F $'\t' -d "$db" -c "$COUNT_SQL" > "$STAGING/postgres/$db.counts.tsv"
done
pg psql -X -At -d spoldzielnia -c "SELECT version_num FROM alembic_version" > "$STAGING/postgres/schema_revision"
pg psql -X -At -d spoldzielnia -c "SELECT id || ' ' || encode(hash, 'hex') FROM security_audit_log ORDER BY id DESC LIMIT 1" \
  > "$STAGING/postgres/audit_head"

step "Object storage (mc mirror)"
"${COMPOSE[@]}" --profile ops run --rm --no-deps --quiet-pull --user "$(id -u):$(id -g)" \
  -v "$STAGING:/backup" -e MODE=dump backup >/dev/null
echo "  $(find "$STAGING/minio" -type f | wc -l | tr -d ' ') objects"

step "Manifest"
python3 - "$STAGING" "$STAMP" "$VERSION_NOW" <<'PY'
import hashlib, json, pathlib, sys
root, stamp, version = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
files = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}
counts = {}
for db in ("spoldzielnia", "keycloak"):
    lines = (root / f"postgres/{db}.counts.tsv").read_text().splitlines()
    counts[db] = {t: int(c) for t, c in (l.split("\t") for l in lines if l)}
objects = [k for k in files if k.startswith("minio/")]
manifest = {
    "format": 1,
    "created_at": stamp,
    "version": version,
    "schema_revision": (root / "postgres/schema_revision").read_text().strip(),
    "audit_head": (root / "postgres/audit_head").read_text().strip(),
    "row_counts": counts,
    "objects": {"count": len(objects), "bytes": sum((root / k).stat().st_size for k in objects)},
    "files": files,
}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"  schema {manifest['schema_revision']}, {len(files)} files, audit head #{manifest['audit_head'].split(' ')[0]}")
PY

step "Encrypt (age)"
tar -C "$STAGING" -cf - . | age -R "$RECIPIENTS" -o "$DIR/$NAME.data.tar.age.partial"
mv "$DIR/$NAME.data.tar.age.partial" "$DIR/$NAME.data.tar.age"
tar -C "$ROOT" -cf - secrets .env | age -R "$SECRETS_RECIPIENTS" -o "$DIR/$NAME.secrets.tar.age.partial"
mv "$DIR/$NAME.secrets.tar.age.partial" "$DIR/$NAME.secrets.tar.age"
(cd "$DIR" && sha256 "$NAME.data.tar.age" "$NAME.secrets.tar.age" > "$NAME.sha256")

if [[ "$KEEP" != "0" ]]; then
  for kind in data.tar.age secrets.tar.age sha256; do
    # Names sort by time (UTC stamp); the newest KEEP of each kind stay.
    find "$DIR" -maxdepth 1 -name "spoldzielnia-*.$kind" | sort -r | tail -n "+$((KEEP + 1))" |
      while read -r old; do rm -f "$old"; done
  done
fi

echo
echo "backup: $DIR/$NAME.data.tar.age"
echo "        $DIR/$NAME.secrets.tar.age"
