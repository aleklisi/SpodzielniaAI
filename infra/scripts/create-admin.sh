#!/usr/bin/env bash
# The first administrator of a new server (6.8): `make create-admin EMAIL=... NAME="First Last"`.
# The production realm has no users. This creates one with MEMBER and ADMIN, a one-time password
# (printed once, here only) and the required actions UPDATE_PASSWORD and CONFIGURE_TOTP, so the
# first login sets a password and the second factor that ADMIN requires. Every later account
# is created in /admin/users (Stage 8).
#   COMPOSE  compose command (default: production file set; lib-ops.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=infra/scripts/lib-ops.sh
source infra/scripts/lib-ops.sh
compose_cmd="${COMPOSE[*]}"; unset COMPOSE; COMPOSE="$compose_cmd" # lib-kcadm.sh takes a string
# shellcheck source=infra/scripts/lib-kcadm.sh
source infra/scripts/lib-kcadm.sh

EMAIL="${EMAIL:-${1:-}}" NAME="${NAME:-${2:-}}"
[[ "$EMAIL" == *@*.* ]] || die 'usage: make create-admin EMAIL=admin@example.org NAME="First Last"'
first="${NAME%% *}" last="${NAME#* }"
[[ "$last" == "$NAME" ]] && last=""

if [[ -n "$(kcadm get users -q "email=$EMAIL" -q exact=true --fields id | jq -r '.[0].id // empty')" ]]; then
  die "$EMAIL already has an account: manage it in /admin/users"
fi
password="$(openssl rand -base64 18 | tr -d '\n/+=')"
kcadm create users -s "username=$EMAIL" -s "email=$EMAIL" -s emailVerified=true -s enabled=true \
  -s "firstName=$first" -s "lastName=$last" \
  -s 'requiredActions=["UPDATE_PASSWORD","CONFIGURE_TOTP"]' >/dev/null
# The password goes over the container's stdin, never into argv.
"${_compose[@]}" exec -T keycloak bash -c '
  cfg=/tmp/kcadm.config
  /opt/keycloak/bin/kcadm.sh config credentials --config $cfg --server http://localhost:8080 \
    --realm master --user admin --password "$(< /run/secrets/keycloak_admin_password)" >/dev/null 2>&1
  IFS= read -r pw
  /opt/keycloak/bin/kcadm.sh set-password --config $cfg -r spoldzielnia --username "$1" \
    --new-password "$pw" --temporary' kcadm "$EMAIL" <<< "$password"
kcadm add-roles --uusername "$EMAIL" --rolename MEMBER --rolename ADMIN

echo "created $EMAIL (MEMBER, ADMIN)"
echo "one-time password: $password"
site="$(env_get SITE_DOMAIN)"
echo "At the first login at https://${site:-spoldzielnia.localhost}/ it asks for a new password and an authenticator app."
