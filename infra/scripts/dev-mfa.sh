#!/usr/bin/env bash
# Dev only: switch the second factor (TOTP) off or on for the running stack.
#   off: Keycloak skips the OTP step at login, the API treats sessions as MFA-satisfied.
#   on:  back to the default (OTP for users who have it, MFA required for EXPERT/ADMIN).
# Usage: infra/scripts/dev-mfa.sh on|off   (COMPOSE overrides the compose command)
set -euo pipefail
cd "$(dirname "$0")/../.."

mode="${1:-}"
case "$mode" in
  off) requirement=DISABLED required=false ;;
  on) requirement=CONDITIONAL required=true ;;
  *) echo "usage: $0 on|off" >&2; exit 2 ;;
esac
read -r -a compose <<< "${COMPOSE:-docker compose -f compose.yaml -f compose.dev.yaml}"
# shellcheck source=infra/scripts/lib-kcadm.sh
source infra/scripts/lib-kcadm.sh

if grep -qs '^SPOLDZIELNIA_ENVIRONMENT=prod' .env; then
  echo "refusing: .env says SPOLDZIELNIA_ENVIRONMENT=prod" >&2; exit 1
fi

# 1. Keycloak: the "browser-amr conditional otp" step of the login flow.
flow="authentication/flows/browser-amr%20forms/executions"
# Send the whole execution back with only the requirement changed: Keycloak treats a missing
# `priority` as 0, which would move the OTP step in front of the password form.
execution="$(kcadm get "$flow" | jq -c --arg r "$requirement" \
  '.[] | select(.displayName == "browser-amr conditional otp") | .requirement = $r | .priority = 20')"
[[ -n "$execution" ]] || { echo "OTP step not found in the browser-amr flow" >&2; exit 1; }
kcadm update "$flow" -b "$execution"

# 2. API and workers: SPOLDZIELNIA_SECURITY__MFA_REQUIRED via MFA_REQUIRED in .env.
touch .env
if grep -q '^MFA_REQUIRED=' .env; then
  sed -i '' -e "s/^MFA_REQUIRED=.*/MFA_REQUIRED=$required/" .env 2>/dev/null \
    || sed -i -e "s/^MFA_REQUIRED=.*/MFA_REQUIRED=$required/" .env
else
  printf 'MFA_REQUIRED=%s\n' "$required" >> .env
fi
"${compose[@]}" up -d --build --wait api

echo "MFA $mode: Keycloak OTP step $requirement, MFA_REQUIRED=$required (existing sessions: log in again)."
