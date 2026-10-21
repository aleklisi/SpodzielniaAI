# Stage 8 — Admin User Management (accounts, passwords, blocking, roles)

**User story:** [US-02](../USER_STORIES_LOG.md#us-02) — *As an administrator I want to manage users
so that I can create new user accounts, reset passwords, block access for a user, force a password
change and manage user roles.*
**Spec refs:** `AGENTS.md` §3 (RLS, provenance unaffected) · `docs/SECURITY.md` (MFA for
EXPERT/ADMIN, step-up, account lock) · chunk 1.5 (JIT provisioning) · chunk 1.6 (sessions, RBAC,
MFA) · chunk 5.6 (shield lock) · chunk 6.6 (admin console, unlock dialog).

> This stage is **outside the original 6-stage plan**. It starts only on an explicit instruction.
> The Definition of Done from `00_OVERVIEW.md` §2.1 applies to every chunk (ruff, pyright strict,
> pytest; npm lint/typecheck/test for frontend chunks).

---

## 0. Current state (what we build on)

- **Keycloak is the only credential store.** The app never sees or stores passwords (`app_user`
  has no password column, by design: *"identified by the IdP, never by a local password"*).
  Users are created by hand in the Keycloak console or in the dev realm export.
- **Roles** are Keycloak realm roles `MEMBER`, `EXPERT`, `ADMIN`, sent in the `roles` claim.
  `DbUserProvisioner` mirrors them into `app_user.roles` on every login. Authorization reads
  `app_user.roles` on each request (`UserDirectory`), so a change in the DB takes effect on the
  next request, while a change only in Keycloak waits for the next login.
- **Blocking** exists only at the app level: `app_user.is_locked` (+ `locked_reason`,
  `locked_at`), set by the anti-distillation shield (5.6). `current_user` refuses a locked
  user with 423 and revokes *that* session family. `POST /admin/users/{id}/unlock` (6.6) clears
  it. Keycloak still lets a locked user log in, and other session families of that user live
  until they are next used.
- **Sessions** (Redis) are indexed by family, not by user, so "sign this user out everywhere"
  is impossible today.
- **The BFF client** `spoldzielnia-backend` has no service account (`serviceAccountsEnabled:
  false`). The api reaches Keycloak on the `backend` network (`http://keycloak:8080`).
- The realm already has `passwordPolicy: length(12) and notUsername and notEmail`,
  brute-force protection, and an SMTP server (`mailpit` in dev, `tools` profile).

## 1. Goals and non-goals

**Goals**
1. **Create** an account: username, email, name, initial roles. The user sets their own
   password (email link or a one-time temporary password), and EXPERT/ADMIN users must also
   enrol TOTP.
2. **Reset a password:** send a reset link by email, or generate a temporary password that is
   shown to the admin once. Either way the user must choose a new password at the next login.
3. **Block / unblock** access: Keycloak refuses the login *and* the app refuses the session;
   every live session of the user ends immediately.
4. **Force a password change** at the next login, optionally signing the user out now.
5. **Manage roles** (`MEMBER`, `EXPERT`, `ADMIN`), effective on the next request, with
   guardrails against lock-out and silent privilege escalation.
6. One screen `/admin/users`: search, filters, user detail, every action audited.

**Non-goals (v1)**
- Deleting users or GDPR erasure (tied to block provenance and consent revocation, 4.x).
  Blocking covers "remove access". Candidate follow-up story.
- Self-service registration, invitations by members, bulk CSV import.
- Managing users of brokered external IdPs beyond block and roles: their password lives
  at the external IdP, so password actions are hidden for them.
- Fine-grained Keycloak admin permissions, groups, custom attributes.
- Resetting a user's TOTP device (useful; candidate follow-up, small: delete OTP credential +
  `CONFIGURE_TOTP` required action).

## 2. Security invariants (must hold after this stage)

| # | Invariant | Enforcement |
|---|-----------|-------------|
| U1 | The app never stores, logs or audits a password. | Temporary passwords are generated server-side (`secrets`), sent to Keycloak with `temporary: true`, returned **once** in the response body with `Cache-Control: no-store`, never written to logs, audit, Redis or the DB. A log-capture test asserts it. |
| U2 | The service account can only manage users, not the realm. | New confidential client `spoldzielnia-admin` (client-credentials only, no browser flows). Service-account roles: `realm-management` → `view-users`, `query-users`, `manage-users` only. No `manage-realm`, `manage-clients`, `impersonation`. Its secret is a Docker secret only the `api` gets. |
| U3 | Only the three app roles can be granted. | The API maps only `MEMBER`/`EXPERT`/`ADMIN` (allowlist in code). The Keycloak client never receives role names from the request directly. |
| U4 | An admin cannot lock out the organisation. | Refused: removing your own `ADMIN`, blocking yourself, demoting or blocking the **last active ADMIN** (count in one transaction with `SELECT … FOR UPDATE`). |
| U5 | Privilege grants need a fresh second factor. | Granting `EXPERT`/`ADMIN`, unblocking, and creating an EXPERT/ADMIN account require `auth_time` ≤ 5 min (step-up, the same mechanism as the shield's `fresh_mfa`). Otherwise 403 `reauthentication_required` and the UI sends the admin to log in again. |
| U6 | A block is total and immediate. | Keycloak `enabled: false` (no new logins, no refresh) + `POST /users/{id}/logout` (IdP sessions) + `app_user.is_locked` + revoke **all** app session families of the user (new per-user index). |
| U7 | Elevated users always have MFA. | Creating or promoting to EXPERT/ADMIN adds the `CONFIGURE_TOTP` required action if the user has no OTP credential. `require_mfa` is unchanged, so the role is unusable until the user logs in with OTP. |
| U8 | Admin and shield locks don't overwrite each other. | `lock_source` `SHIELD` / `ADMIN`. Unblocking clears only the admin lock unless the admin explicitly also clears the shield lock (the existing 6.6 unlock flow, which already shows the risk history). |
| U9 | Every action is attributable. | Hash-chained audit events (§3.4) with actor, target and non-secret details (roles before/after, delivery method of a reset, reason code). ADMIN + MFA on every route (`elevated_router`). |
| U10 | Keycloak and `app_user` don't drift silently. | Keycloak is written first, then the mirror in `app_user`. If the mirror fails, the request returns 502 and the next login reconciles (login already mirrors roles and now also the enabled flag). A `users-reconcile` CLI fixes drift on demand. |

## 3. Design

### 3.1 Keycloak admin client and realm setup

- `auth/keycloak_admin.py`: `KeycloakAdmin`, an httpx client on `keycloak_admin.base_url`
  (default `http://keycloak:8080`) with a client-credentials token cached until shortly before
  `exp`, retried once on 401. Typed methods only for what this stage uses:
  `search_users`, `get_user`, `create_user` (returns the id from `Location`), `update_user`
  (enabled, names, email, `requiredActions`), `realm_roles_of`, `add_realm_roles`,
  `remove_realm_roles`, `reset_password(temporary=True)`, `execute_actions_email`,
  `logout`, `credentials_of` (to detect OTP), `federated_identities_of`.
- Errors map to a small set: `UserExists` (409), `NotFound` (404), `PolicyViolation` (400,
  Keycloak's password-policy message, never the password), `IdpUnavailable` (503).
- Settings `KeycloakAdminSettings`: `base_url`, `realm`, `client_id`, `client_secret` (Docker
  secret `keycloak_admin_client_secret`), `timeout_seconds`, `reset_link_lifespan_seconds`
  (default 24 h), `step_up_max_age_seconds` (default 300).
- **Realm setup.** `import-realm` skips an existing realm, so existing dev and prod realms need an
  idempotent setup step: `infra/scripts/keycloak-setup.sh` (kcadm in the keycloak container,
  same pattern as `dev-mfa.sh`, plus `make keycloak-setup`). It creates or updates the
  `spoldzielnia-admin` client, its service-account roles and its secret, and sets the
  `UPDATE_PASSWORD` and `CONFIGURE_TOTP` required actions to enabled. The realm export gets the
  same client for fresh installs. `init-secrets.sh` generates the new secret.

### 3.2 Data model and sessions (migration `0013_user_admin`)

```
app_user  + lock_source   SHIELD|ADMIN|NULL   (existing rows with is_locked → SHIELD)
          + locked_by     → app_user (admin who blocked; NULL for the shield)
          + created_by    → app_user (NULL for JIT-provisioned users)
          + last_login_at timestamptz (set by the provisioner)
```

- An account created by an admin is **pre-provisioned** in `app_user` with the Keycloak user id
  as `oidc_subject` and the configured issuer. The first login then updates it through the
  existing `(issuer, subject)` upsert, with no special case.
- The provisioner also mirrors `enabled = false` from Keycloak, if it shows up in a token
  (defence in depth; Keycloak refuses those logins anyway).
- `SessionStore`: new index `user:{user_id}:families` (a set, written when a family is created,
  expiring with the family). `revoke_user(user_id)` revokes every family and its refresh tokens.
  `current_user` keeps revoking on 423 as a second layer.
- Grants: the app role may `UPDATE` only `roles`, `is_locked`, `lock_source`, `locked_*`,
  `last_login_at`, `display_name`, `email` on `app_user`, as today plus the new columns.

### 3.3 `UserAdminService` (`auth/user_admin.py`)

One service, used only by the API. Every method takes the acting `Principal`, checks the
guardrails, calls Keycloak, updates the mirror and writes the audit event in one DB transaction.

| Operation | Keycloak | App |
|---|---|---|
| `create(username, email, first, last, roles, password_mode)` | `create_user(enabled, emailVerified=false, requiredActions=[UPDATE_PASSWORD, VERIFY_EMAIL?, CONFIGURE_TOTP if elevated])`, add roles. `password_mode`: `email` → `execute-actions-email`, `temporary` → `reset_password(temporary)` | pre-provision `app_user`, audit `USER_CREATED` |
| `set_roles(user, roles)` | add/remove the delta; add `CONFIGURE_TOTP` when becoming elevated without OTP | mirror `roles`; revoke sessions on any removal; audit `USER_ROLES_CHANGED` (before/after) |
| `block(user, reason)` | `enabled=false`, `logout` | `is_locked`, `lock_source=ADMIN`, `locked_by`, `revoke_user`; audit `USER_BLOCKED` |
| `unblock(user, also_clear_shield)` | `enabled=true` | clear the admin lock (and the shield lock only if asked); audit `USER_UNBLOCKED` |
| `reset_password(user, mode)` | `email` → `execute-actions-email [UPDATE_PASSWORD]`; `temporary` → `reset_password(temporary=true)` | `revoke_user`; audit `USER_PASSWORD_RESET` with `mode` only |
| `require_password_change(user, sign_out)` | add `UPDATE_PASSWORD` to `requiredActions`; `logout` if `sign_out` | `revoke_user` if `sign_out`; audit `USER_PASSWORD_CHANGE_REQUIRED` |
| `sign_out(user)` | `logout` | `revoke_user`; audit `USER_SESSIONS_REVOKED` |

- **Reset delivery decision:** `email` is the default when the realm has SMTP and the user has an
  email. `temporary` exists for cooperatives without mail; the password is 16 characters from an
  unambiguous alphabet, meets the realm policy, and the UI shows it once with "Kopiuj".
- **Brokered users** (with a federated identity): password operations → 409
  `external_identity`. Block, roles and sign-out work.
- **Listing** comes from Keycloak (`search`, `first`/`max`, so it includes users who never logged
  in), joined with `app_user` by subject for lock state, risk score and last login. Paginated,
  max 100 per page.

### 3.4 Admin API (`api/admin_users.py`, prefix `/admin/users`, ADMIN + MFA)

| Method & path | Purpose |
|---|---|
| `GET /admin/users?q=&role=&status=&first=&max=` | Search and filter (status: `active`, `blocked`, `shield_locked`, `pending_setup`). |
| `GET /admin/users/{id}` | Detail: profile, roles, enabled, lock (source, reason, by, at), required actions, has OTP, external identity, last login, risk score. |
| `POST /admin/users` | Create (step-up if elevated roles). 201 + `temporary_password` only in `temporary` mode. |
| `PUT /admin/users/{id}/roles` | Replace the role set (step-up on grants of EXPERT/ADMIN). |
| `POST /admin/users/{id}/block` · `/unblock` | Block with a reason code + optional note (max 200 chars, not shown to the user). Unblock requires step-up. |
| `POST /admin/users/{id}/password-reset` | `{mode: email\|temporary}`. |
| `POST /admin/users/{id}/require-password-change` | `{sign_out: bool}`. |
| `POST /admin/users/{id}/sign-out` | End all sessions. |
| `POST /admin/users/{id}/unlock` | Existing 6.6 shield unlock, moved into this router, same contract. |

- Rate limit: 30 mutating calls per admin per minute (Redis, as in the playground); temporary
  password resets 10 per hour per admin.
- `{id}` is the `app_user.id` (UUID). The Keycloak id never leaves the backend.
- Responses never contain passwords except the one-time `temporary_password` field, with
  `Cache-Control: no-store`.
- New audit events: `USER_CREATED`, `USER_ROLES_CHANGED`, `USER_BLOCKED`, `USER_UNBLOCKED`,
  `USER_PASSWORD_RESET`, `USER_PASSWORD_CHANGE_REQUIRED`, `USER_SESSIONS_REVOKED`. The 6.6
  alerts list adds `USER_ROLES_CHANGED` with a grant of ADMIN.
- Metrics: `user_admin_actions_total{action,outcome}`, `keycloak_admin_seconds{op}`.

### 3.5 Frontend (`/admin/users`, nav item "Użytkownicy", role ADMIN)

- **List:** search box (name, username, email), filters by role and status, table with name,
  username, email, roles (badges), status badge (Aktywny / Zablokowany / Zablokowany przez system
  ochrony / Oczekuje na konfigurację), last login. "Dodaj użytkownika" button.
- **Create dialog:** username, email, first and last name, role checkboxes (MEMBER pre-checked),
  password delivery (Link e-mailem / Hasło tymczasowe). After saving with the temporary mode: a
  one-time panel with the password, "Kopiuj" and a warning that it won't be shown again.
- **User detail** (side panel): profile, roles editor with a confirm step that names what
  changes ("Nadajesz rolę Administrator" + MFA notice), status with block/unblock (reason
  select + note), "Wymuś zmianę hasła" (checkbox "wyloguj teraz"), "Resetuj hasło", "Wyloguj ze
  wszystkich urządzeń". Disabled actions explain why (your own account, last administrator,
  external identity).
- Step-up: a 403 `reauthentication_required` shows the existing "Zaloguj ponownie z MFA" flow
  (`RequireRole` step-up component) and repeats nothing automatically.
- The existing `UnlockDialog` (shield lock) is reused from the detail panel. The security
  dashboard links users to `/admin/users/{id}`.
- Same conventions as 6.6/7.6: typed client from `openapi.json`, MSW tests, Tailwind tokens,
  light/dark, Polish copy.

## 4. Chunks

### Chunk 8.1 — Keycloak admin client and realm setup
- `spoldzielnia-admin` client in the realm export, `keycloak-setup.sh` + `make keycloak-setup`,
  new secret in `init-secrets.sh` and compose (api only), `KeycloakAdminSettings`,
  `auth/keycloak_admin.py`.
- **Tests:** unit (respx) — token caching and refresh on 401, error mapping (409/404/400/5xx),
  `Location` id parsing, no request body logged; integration (stack) — the service account can
  create and disable a user but gets 403 on a realm update and on client listing (U2);
  `keycloak-setup.sh` is idempotent (run twice).

### Chunk 8.2 — Data model, per-user session index, audit events
- Migration `0013_user_admin` (columns, backfill `lock_source=SHIELD`, grants), provisioner sets
  `last_login_at` and keeps pre-provisioned rows, `SessionStore.revoke_user`, new `EventType`s.
- **Tests:** unit — `revoke_user` ends every family of the user and none of another user's,
  refresh after revocation fails; integration — migration up/down, a pre-provisioned row is
  adopted by the first login (same id), the shield lock still works and is labelled `SHIELD`.

### Chunk 8.3 — `UserAdminService` and guardrails
- `auth/user_admin.py` per §3.3, temporary password generator, step-up check helper shared with
  the shield.
- **Tests:** unit with a fake Keycloak — every operation's Keycloak calls, mirror and audit;
  U4 (self-demote, self-block, last admin, two admins demoting each other concurrently → one
  wins); U5 (stale `auth_time` → `reauthentication_required`); U7 (`CONFIGURE_TOTP` added on
  promotion without OTP); U8 (admin unblock keeps a shield lock); U1 (log capture and audit rows
  never contain the temporary password); Keycloak failure after a partial change leaves the mirror
  untouched and returns 502.

### Chunk 8.4 — Admin API `/admin/users`
- Router per §3.4, unlock route moved, rate limits, metrics, regenerated `openapi.json`.
- **Tests:** authz matrix (MEMBER, EXPERT, ADMIN without MFA → 403); unknown role name → 422;
  `Cache-Control: no-store` on the temporary-password response; brokered user password reset →
  409; pagination limits; a blocked user's existing session gets 423 on the next request and its
  refresh fails (U6).

### Chunk 8.5 — Frontend `/admin/users`
- `src/lib/users-admin.ts`, `src/components/admin/users/*`, `src/app/admin/users/page.tsx`, nav
  entry.
- **Tests:** Vitest + MSW — list filters, create with temporary password shows it once and clears
  it on close, role confirm names the grant, guardrail explanations on own account / last admin,
  step-up link on 403, password actions hidden for external identities.

### Chunk 8.6 — E2E, security review and docs
- Playwright scenario 10: an admin creates `e2e-user-<ts>` with a temporary password; the new user
  logs in, is forced to set a password and lands in the app; the admin grants EXPERT (user must
  enrol TOTP — skipped with `make mfa-off`), forces a password change, blocks the user (their
  open page gets the lock screen, a new login is refused by Keycloak), unblocks; the test user is
  disabled at the end. A member gets 403 on `/admin/users`.
- `SECURITY_REVIEW.md` (API1/API3/API5, ASVS V2/V3), README section, story log delivery list.

**Stage 8 exit criteria:** chunks 8.1–8.6 green, U1–U10 covered by tests, scenario 10 passes
against the compose stack with MFA on and off.

## 5. Decisions taken (change before 8.1 if you disagree)

| Topic | Decision | Alternative |
|---|---|---|
| Source of truth | Keycloak for credentials, roles and enabled; `app_user` mirrors. | Roles only in the app DB (breaks the token's `roles` claim and the IdP's view). |
| Password reset | Email link by default, temporary password as a fallback for no-mail installs. | Email only (simpler, needs SMTP in prod). |
| Who manages users | Every ADMIN. | A separate `USER_ADMIN` role (more roles to explain; revisit if admins grow beyond a few people). |
| Deleting users | Not in v1; block instead. | Hard delete (conflicts with block provenance and the audit trail). |
| Step-up window | 5 minutes, for grants, unblocks and elevated account creation. | Every mutating action (stricter, more logins). |

## Implementation notes

### 8.1 (done)
- `auth/keycloak_admin.py` (`KeycloakAdmin`): client-credentials token cached until 30 s before
  `exp`, one renewal on 401; errors `UserExists` 409, `UserNotFound` 404, `PasswordRejected`
  (Keycloak's message key), `ActionEmailFailed`, `IdpUnavailable` (transport, 5xx, no secret),
  `KeycloakAdminError` (anything else, `key` for 400s). Only method, status and duration are
  logged. Search is infix (`*q*`): Keycloak's default is prefix-only.
- Role mapping uses the user's `role-mappings/realm/available` (add) and assigned mappings
  (remove) instead of `GET /roles/{name}`, so a role outside what Keycloak offers the user
  (e.g. `realm-admin`) fails before any write. The service account *can* list realm role names
  (Keycloak needs that for mapping); it cannot create roles, update the realm or list clients
  (integration test).
- `infra/scripts/lib-kcadm.sh` (shared with `dev-mfa.sh`), `infra/scripts/keycloak-setup.sh`
  and `make keycloak-setup`: creates or updates the client, sets its secret inside the
  container, grants exactly `view-users`/`query-users`/`manage-users` (removes any other
  realm-management role), enables the three required actions. Idempotent (tested twice in a row).
- Secret `keycloak_admin_client_secret`: generated by `init-secrets.sh`, mounted in `keycloak`
  (realm import placeholder `${KEYCLOAK_ADMIN_CLIENT_SECRET}`) and in `api` only.
- Dev only: Keycloak's internal port on `127.0.0.1:8180` for host-side integration tests.
- Without the secret (`keycloak_admin.client_secret` unset) the client reports
  `configured = False` and every call raises `IdpUnavailable`; the API answers 503 (8.4).

### 8.2 (done)
- **Deviation from §3.2:** instead of one `lock_source` column, an admin block has its own
  columns `blocked_at`, `blocked_by`, `blocked_reason` (+ optional `blocked_note`, CHECK: a
  block always has a reason). The shield keeps `is_locked`/`locked_*`. `User.refused` (either
  set) is what `UserDirectory` and the provisioner report as `is_locked`, so authorization code
  did not change, and U8 holds by construction: neither path can clear the other's flag.
- Also new: `username` (`preferred_username`, indexed lower-case), `created_by`,
  `last_login_at`, `required_actions` (mirror for the list status; cleared on login because
  Keycloak issues tokens only after required actions are done).
- `OidcIdentity.username`; the provisioner sets `username`, `last_login_at`, clears
  `required_actions`, and adopts a pre-created row through the existing `(issuer, subject)`
  upsert.
- Sessions: `user:{id}:families` set, TTL = the longest family (`EXPIREAT NX` then `GT`);
  `SessionStore.delete_user_families`, `SessionManager.revoke_user`.
- Audit events `USER_CREATED`, `USER_ROLES_CHANGED`, `USER_BLOCKED`, `USER_UNBLOCKED`,
  `USER_PASSWORD_RESET`, `USER_PASSWORD_CHANGE_REQUIRED`, `USER_SESSIONS_REVOKED`,
  `USERS_SYNCED`.

### 8.3 (done)
- `auth/user_admin.py` (`UserAdminService`) with a `UserIdp` protocol (`KeycloakAdmin` in
  production, `tests/fixtures/keycloak.py` in tests) and `UserAdminError(code, status)`.
- Every account is a `MEMBER` (always added); usernames are lower-case `[a-z0-9._-]{3,64}`
  and may not start with `service-account-`.
- U4 locking: every ADMIN row is locked `FOR UPDATE` in id order **before** the target row,
  so two admins demoting each other serialize (no deadlock); the second sees `last_admin`.
  Tested with both transactions open at once.
- `create` undoes the Keycloak user if roles, the password step or the mirror fail, so the
  username stays free; a failed e-mail answers `502 email_failed` (use the temporary mode).
- `CONFIGURE_TOTP` is added only while `security.mfa_required` is on (not with `make mfa-off`).
- Removing a role takes effect on the next request (roles are read from `app_user`), so it
  does not end sessions; a block, a password reset, sign-out and (optionally) a forced change do.
- **Deviation from §3.3 (listing):** the list and its filters read `app_user`, not a Keycloak
  search. Admin-created users are pre-provisioned, and `sync` (U10) imports users created in
  the Keycloak console, mirrors roles/required actions and blocks users disabled there
  (`idp_disabled`); it never lifts an app block. The detail view reads Keycloak live and falls
  back to the mirror when Keycloak is down.

### 8.4 (done)
- `api/admin_users.py`, router `/admin/users` (ADMIN + MFA): `GET ""` (q, role, status,
  offset, limit ≤ 100), `POST ""` (201, `temporary_password` only in temporary mode),
  `POST /sync`, `GET /{id}` (mirror + live Keycloak `idp`, `other_active_admins` for the UI's
  last-admin hint), `PUT /{id}/roles`, `POST /{id}/block|unblock|password-reset|
  require-password-change|sign-out`.
- `reauthentication_required` (403) carries the same `step_up` URL as `mfa_required`, so the
  existing frontend step-up flow handles it. Keycloak unreachable → 503 `idp_unavailable`
  (`Retry-After`), other Keycloak errors → 502 `idp_error`; the list keeps working from the
  mirror.
- Limits: `keycloak_admin.mutations_per_minute` (30) on every mutation, and
  `temporary_passwords_per_hour` (10) on top for temporary passwords (create and reset).
- The shield unlock stays at `POST /admin/users/{id}/unlock` in `api/admin.py` (same path
  family, unchanged contract) instead of moving.
- `Cache-Control: no-store` is already on every API response (`core/headers.py`).
- E-mail addresses get a shape check (no `email-validator` dependency); Keycloak validates too.
- Metrics `user_admin_actions_total{action,outcome}`, `keycloak_admin_seconds{method}`.
- Tests app state hook: `app.state.user_idp` replaces Keycloak with the in-memory fake.

### 8.5 (done)
- `frontend/src/lib/users-admin.ts` (hooks, Polish labels and refusal messages, guardrail
  helpers `lockoutReason`, `grantsElevated`), `components/admin/users/{UsersConsole,
  UserPanel,CreateUserDialog,OneTimePassword}.tsx`, page `/admin/users`, nav "Użytkownicy".
- List: debounced search, role and status filters, pages of 25; the detail panel opens beside
  the table (below it on narrow screens). `/admin/users?user=<id>` opens a user; the security
  dashboard's user table links there ("konto").
- A temporary password lives only in dialog state and is dropped when the dialog closes.
- Grants of EXPERT/ADMIN go through a confirmation naming the role (and the MFA enrolment the
  user will face); the API's step-up 403 shows the existing "Zaloguj ponownie z MFA" notice in
  the panel and resets the checkboxes.
- Own account / last active admin: block button and the ADMIN checkbox are disabled with the
  reason; external identities get no password actions.
- The shield unlock reuses `UnlockDialog` (its prop type is now the four fields it reads).
- Audit labels for the new `USER_*` events in the security console.
- Tests: `users.test.tsx` (9: list and filters, one-time password, grant confirmation,
  guardrails, step-up, external identity, block body, error mapping, helpers).

