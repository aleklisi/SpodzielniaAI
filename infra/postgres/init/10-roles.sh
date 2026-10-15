#!/usr/bin/env bash
# Runs once on an empty data directory (docker-entrypoint-initdb.d), as the superuser.
# Creates the role model from docs/implementation/01 (gap G8):
#   spoldzielnia_owner  owns schema + tables, used only by migrations
#   spoldzielnia_app    runtime role: NOSUPERUSER NOBYPASSRLS, DML only (granted by migrations)
#   keycloak            owns the separate `keycloak` database
# Passwords are read from Docker secrets and passed as psql variables (never interpolated
# into SQL text by the shell).
set -euo pipefail

secret() { tr -d '\n' < "/run/secrets/$1"; }

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$(secret pg_owner_password)" \
  -v app_pw="$(secret pg_app_password)" \
  -v kc_pw="$(secret pg_keycloak_password)" <<'SQL'
CREATE ROLE spoldzielnia_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
  PASSWORD :'owner_pw';
CREATE ROLE spoldzielnia_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS
  NOREPLICATION CONNECTION LIMIT 100 PASSWORD :'app_pw';
CREATE ROLE keycloak LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD :'kc_pw';

CREATE DATABASE spoldzielnia OWNER spoldzielnia_owner;
CREATE DATABASE keycloak OWNER keycloak;

REVOKE ALL ON DATABASE spoldzielnia FROM PUBLIC;
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE spoldzielnia TO spoldzielnia_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname spoldzielnia <<'SQL'
-- Only the owner may create objects; the app role can use the schema but never alter it.
ALTER SCHEMA public OWNER TO spoldzielnia_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO spoldzielnia_app;

-- Tables created later by migrations (as the owner) are DML-accessible to the app role.
-- TRUNCATE/REFERENCES/TRIGGER are deliberately not granted.
ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO spoldzielnia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO spoldzielnia_app;
ALTER DEFAULT PRIVILEGES FOR ROLE spoldzielnia_owner IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO spoldzielnia_app;
SQL

echo "spoldzielnia: roles and databases created"
