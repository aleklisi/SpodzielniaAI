#!/usr/bin/env bash
# Disaster-recovery rehearsal (6.8): `make dr-rehearsal`, and the CI job `dr-rehearsal`.
# Proves that the backup of the running stack restores onto a bare installation:
#   1. backup.sh of the running (source) stack, with a throwaway age key
#   2. a fresh copy of the checkout without ./secrets, .env or any volume (the "new server")
#   3. restore.sh there, in its own compose project (STRICT=1: row counts and objects must equal
#      the manifest; Qdrant rebuilt from Postgres; the audit chain verified through the backup)
#   4. smoke.sh against the restored stack
#   5. the rehearsal project is removed with its volumes, the source stack started again
# The source stack's volumes are never touched. Its containers stop meanwhile: both projects
# use the same subnets and ports.
#   SOURCE_COMPOSE  compose command of the source stack (default: the dev file set)
#   DR_PROJECT      compose project of the restored copy (default: spoldzielnia-dr)
#   DR_MODELS       copy (default): the offline models come from the source's `models` volume
#                   (they are not in backups) | download: `models-init` fetches them
#   KEEP_DR=1       leave the restored copy running for inspection (`down -v` it yourself)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
SOURCE_COMPOSE="${SOURCE_COMPOSE:-docker compose -f compose.yaml -f compose.dev.yaml}"
read -r -a SOURCE <<< "$SOURCE_COMPOSE"
FILES=("${SOURCE[@]:2}") # the -f arguments after `docker compose`
PROJECT="${DR_PROJECT:-spoldzielnia-dr}"
SOURCE_PROJECT="$("${SOURCE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
[[ "$PROJECT" != "$SOURCE_PROJECT" ]] || { echo "DR_PROJECT must differ from the source project" >&2; exit 2; }
command -v age age-keygen >/dev/null || { echo "age is not installed" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/spoldzielnia-dr.XXXXXX")"
chmod 700 "$WORK"
TARGET=(docker compose -p "$PROJECT" "${FILES[@]}")
source_down=0
cleanup() {
  local status=$?
  if [[ "${KEEP_DR:-0}" != "1" ]]; then
    (cd "$WORK/server" 2>/dev/null && "${TARGET[@]}" --profile ops --profile tools down --volumes --remove-orphans >/dev/null 2>&1) || true
    rm -rf "$WORK"
  else
    echo "kept: $WORK/server (project $PROJECT); remove with: cd $WORK/server && ${TARGET[*]} down -v"
  fi
  if ((source_down)) && [[ "${KEEP_DR:-0}" != "1" ]]; then
    echo "== starting the source stack again"
    cd "$ROOT"
    "${SOURCE[@]}" up -d --wait --wait-timeout 600 >/dev/null || echo "could not restart the source stack" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

echo "== 1. Backup of the source stack ($SOURCE_PROJECT)"
age-keygen -o "$WORK/identity.txt" 2>/dev/null
age-keygen -y "$WORK/identity.txt" > "$WORK/recipients.txt"
COMPOSE="$SOURCE_COMPOSE" BACKUP_DIR="$WORK/bundles" BACKUP_RECIPIENTS="$WORK/recipients.txt" BACKUP_KEEP=0 \
  infra/scripts/backup.sh
DATA="$(ls "$WORK"/bundles/*.data.tar.age)" SECRETS="$(ls "$WORK"/bundles/*.secrets.tar.age)"

echo "== 2. A bare copy of the checkout"
mkdir -p "$WORK/server"
# Tracked and new files, nothing ignored: no ./secrets, no .env, no node_modules.
git ls-files -z --cached --others --exclude-standard | rsync -a --from0 --files-from=- ./ "$WORK/server/"
[[ ! -e "$WORK/server/secrets" && ! -e "$WORK/server/.env" ]]

echo "== 3. Restore into project $PROJECT"
"${SOURCE[@]}" down
source_down=1
if [[ "${DR_MODELS:-copy}" == "copy" ]]; then
  # Labelled as compose would, so the project adopts the volume.
  docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=models \
    "${PROJECT}_models" >/dev/null
  docker run --rm --network none -v "${SOURCE_PROJECT}_models:/from:ro" -v "${PROJECT}_models:/to" \
    --entrypoint cp "$("${SOURCE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["postgres"]["image"])')" \
    -a /from/. /to/
fi
cd "$WORK/server"
COMPOSE="${TARGET[*]}" STRICT=1 BACKUP_IDENTITY="$WORK/identity.txt" DATA="$DATA" SECRETS="$SECRETS" \
  infra/scripts/restore.sh
if [[ "${DR_MODELS:-copy}" == "download" ]]; then
  # Compose needs ./secrets to parse, so the models come once restore.sh has installed them.
  "${TARGET[@]}" --profile tools run --rm models-init
  "${TARGET[@]}" restart worker-core worker-media
fi

echo "== 4. Smoke test of the restored stack"
COMPOSE="${TARGET[*]}" infra/scripts/smoke.sh

echo
echo "dr-rehearsal: the backup restored onto a bare installation and passed every check"
