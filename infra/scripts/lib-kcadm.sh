# Sourced by the Keycloak scripts: `kcadm <args>` runs Keycloak's admin CLI in the keycloak
# container against the spoldzielnia realm, logged in as the bootstrap admin. The admin
# password is read inside the container and never passes through the host.
# Needs: COMPOSE (default: the dev compose command).
read -r -a _compose <<< "${COMPOSE:-docker compose -f compose.yaml -f compose.dev.yaml}"

kc_exec() {
  "${_compose[@]}" exec -T keycloak bash -c "$@"
}

kcadm() {
  "${_compose[@]}" exec -T keycloak bash -c '
    cfg=/tmp/kcadm.config
    /opt/keycloak/bin/kcadm.sh config credentials --config $cfg --server http://localhost:8080 \
      --realm master --user admin --password "$(< /run/secrets/keycloak_admin_password)" >/dev/null 2>&1
    /opt/keycloak/bin/kcadm.sh "$@" --config $cfg -r spoldzielnia' kcadm "$@"
}
