#!/usr/bin/env bash
# Idempotent Keycloak setup for user management (Stage 8), for realms that already exist
# (`--import-realm` skips an existing realm; prod never imports the dev export):
#   - confidential client `spoldzielnia-admin`: client credentials only, secret from the Docker
#     secret `keycloak_admin_client_secret` (read inside the container);
#   - its service account gets realm-management view-users, query-users, manage-users and
#     nothing else (any other realm-management role is removed);
#   - the UPDATE_PASSWORD, CONFIGURE_TOTP and VERIFY_EMAIL required actions are enabled.
# Usage: infra/scripts/keycloak-setup.sh   (COMPOSE overrides the compose command)
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=infra/scripts/lib-kcadm.sh
source infra/scripts/lib-kcadm.sh

CLIENT=spoldzielnia-admin
ROLES=(view-users query-users manage-users)

kc_exec 'test -s /run/secrets/keycloak_admin_client_secret' || {
  echo "keycloak has no keycloak_admin_client_secret: run 'make secrets' and recreate keycloak" >&2
  exit 1
}

# 1. The client (created or brought back to the expected settings, secret included).
id="$(kcadm get clients -q "clientId=$CLIENT" --fields id | jq -r '.[0].id // empty')"
body='{
  "clientId": "'$CLIENT'",
  "name": "Spółdzielnia AI user management",
  "protocol": "openid-connect",
  "enabled": true,
  "publicClient": false,
  "clientAuthenticatorType": "client-secret",
  "standardFlowEnabled": false,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": true,
  "frontchannelLogout": false,
  "fullScopeAllowed": true,
  "consentRequired": false,
  "attributes": { "access.token.lifespan": "300" }
}'
if [[ -z "$id" ]]; then
  kcadm create clients -b "$body" >/dev/null
  id="$(kcadm get clients -q "clientId=$CLIENT" --fields id | jq -r '.[0].id')"
  echo "  created  client $CLIENT"
else
  kcadm update "clients/$id" -b "$body" >/dev/null
  echo "  updated  client $CLIENT"
fi
# The secret is set inside the container: it never appears on the host command line.
kc_exec '
  cfg=/tmp/kcadm.config
  /opt/keycloak/bin/kcadm.sh config credentials --config $cfg --server http://localhost:8080 \
    --realm master --user admin --password "$(< /run/secrets/keycloak_admin_password)" >/dev/null 2>&1
  printf "{\"secret\":\"%s\"}" "$(< /run/secrets/keycloak_admin_client_secret)" \
    | /opt/keycloak/bin/kcadm.sh update "clients/'"$id"'" -f - --config $cfg -r spoldzielnia'

# 2. Service-account roles: exactly ROLES from realm-management.
sa="service-account-$CLIENT"
role_args=()
for role in "${ROLES[@]}"; do role_args+=(--rolename "$role"); done
kcadm add-roles --uusername "$sa" --cclientid realm-management "${role_args[@]}" >/dev/null
sa_id="$(kcadm get users -q "username=$sa" -q exact=true --fields id | jq -r '.[0].id')"
rm_id="$(kcadm get clients -q clientId=realm-management --fields id | jq -r '.[0].id')"
extra="$(kcadm get "users/$sa_id/role-mappings/clients/$rm_id" \
  | jq -r --argjson keep "$(printf '%s\n' "${ROLES[@]}" | jq -R . | jq -s .)" \
      '.[] | select(.name as $n | $keep | index($n) | not) | .name')"
for role in $extra; do
  kcadm remove-roles --uusername "$sa" --cclientid realm-management --rolename "$role"
  echo "  removed  realm-management/$role from $sa"
done
echo "  roles    $sa: ${ROLES[*]}"

# 3. Required actions the API relies on.
for action in UPDATE_PASSWORD CONFIGURE_TOTP VERIFY_EMAIL; do
  kcadm update "authentication/required-actions/$action" -s enabled=true >/dev/null
done
echo "  enabled  required actions UPDATE_PASSWORD, CONFIGURE_TOTP, VERIFY_EMAIL"
