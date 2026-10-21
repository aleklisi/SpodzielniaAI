/**
 * Scenario 10 (Stage 8, US-02): an admin manages a user's whole lifecycle in /admin/users.
 *
 * Creates `e2e-user-…` with a temporary password; the new user logs in, must set a password and
 * lands in the app. The admin forces another password change (signing the user out), blocks the
 * user (open page signed out, Keycloak refuses the login), unblocks, and grants EXPERT. The test
 * user ends blocked, so leftovers of earlier runs can't log in. Works with `make mfa-off` too.
 */

import { expect, test, type Browser, type Page } from "@playwright/test";

import { apiCall, mfaOff, session, SITE } from "./support/stack";

const strongPassword = () => `E2e-Haslo-${Math.random().toString(36).slice(2, 10)}!`;

async function newPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext();
  return context.newPage();
}

/** Keycloak's login form, then the "update password" form if Keycloak asks for it. */
async function keycloakLogin(page: Page, username: string, password: string, newPassword?: string): Promise<void> {
  await page.fill("#username", username);
  await page.fill("#password", password);
  await page.click("#kc-login");
  if (newPassword) {
    await page.locator("#password-new").fill(newPassword);
    await page.locator("#password-confirm").fill(newPassword);
    await page.locator("#kc-passwd-update-form [type=submit]").click();
  }
}

async function loginAs(page: Page, username: string, password: string, newPassword?: string): Promise<void> {
  await page.goto("/api/auth/login?return_to=%2F");
  await keycloakLogin(page, username, password, newPassword);
  await page.waitForURL((url) => url.origin === SITE && url.pathname === "/");
}

async function openUser(admin: Page, username: string) {
  await admin.getByLabel("Szukaj").fill(username);
  await admin.getByRole("button", { name: new RegExp(`^Szczegóły: .*`) }).first().waitFor();
  const row = admin.getByRole("row").filter({ hasText: username });
  await row.getByRole("button", { name: /^Szczegóły:/ }).click();
  const panel = admin.getByRole("region", { name: "Szczegóły użytkownika" });
  await expect(panel.getByText(username, { exact: true })).toBeVisible();
  return panel;
}

test("members cannot see or call the user console", async ({ browser }) => {
  const member = await session(browser, "member1", "/admin/users");
  await expect(member.getByRole("heading", { name: "Brak dostępu" })).toBeVisible();
  expect((await apiCall(member, "GET", "/admin/users")).status()).toBe(403);
  expect((await apiCall(member, "POST", "/admin/users", { username: "x-member" })).status()).toBe(403);
});

test("an admin creates, resets, blocks, unblocks and promotes a user", async ({ browser }) => {
  test.setTimeout(300_000);
  const admin = await session(browser, "admin1", "/admin/users");
  await expect(admin.getByRole("heading", { name: "Użytkownicy" })).toBeVisible();
  const username = `e2e-user-${Date.now().toString(36)}`;

  // 1. Create with a temporary password, shown once.
  await admin.getByRole("button", { name: "Dodaj użytkownika" }).click();
  const form = admin.getByRole("form", { name: "Nowe konto" });
  await form.getByLabel(/Nazwa użytkownika/).fill(username);
  await form.getByLabel("E-mail", { exact: true }).fill(`${username}@example.test`);
  await form.getByLabel("Imię").fill("Test");
  await form.getByLabel("Nazwisko").fill("E2E");
  await form.getByLabel(/Hasło tymczasowe/).check();
  await form.getByRole("button", { name: "Utwórz konto" }).click();
  const output = admin.getByLabel("Hasło tymczasowe", { exact: true });
  await expect(output).toHaveText(/^\S{16}$/);
  const temporary = ((await output.textContent()) ?? "").trim();
  await admin.getByRole("button", { name: "Gotowe" }).click();
  await expect(admin.getByText(temporary)).toHaveCount(0);

  // 2. First login: Keycloak makes the user choose a password, then the app opens.
  const user = await newPage(browser);
  let password = strongPassword();
  await loginAs(user, username, temporary, password);
  expect((await (await apiCall(user, "GET", "/me")).json()).roles).toEqual(["MEMBER"]);

  // 3. Force a password change and sign the user out everywhere.
  let panel = await openUser(admin, username);
  await expect(panel.getByText("Aktywny", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Wymuś zmianę hasła" }).click();
  let dialog = admin.locator("dialog[open]");
  await dialog.getByLabel(/Wyloguj teraz/).check();
  await dialog.getByRole("button", { name: "Wymuś" }).click();
  await expect(admin.getByText("Zmiana hasła będzie wymagana przy logowaniu.")).toBeVisible();
  expect((await apiCall(user, "GET", "/me")).status()).toBe(401);
  const next = strongPassword();
  await loginAs(user, username, password, next);
  password = next;

  // 4. Block: the open session ends and Keycloak refuses a new login.
  panel = await openUser(admin, username);
  await panel.getByRole("button", { name: "Zablokuj" }).click();
  dialog = admin.locator("dialog[open]");
  await dialog.getByLabel("Powód").selectOption("security");
  await dialog.getByLabel(/Notatka/).fill("E2E: blokada testowa");
  await dialog.getByRole("button", { name: "Zablokuj konto" }).click();
  await expect(panel.getByText("Zablokowane: Bezpieczeństwo konta")).toBeVisible();
  expect((await apiCall(user, "GET", "/me")).status()).toBe(401);
  const refused = await newPage(browser);
  await refused.goto("/api/auth/login?return_to=%2F");
  await keycloakLogin(refused, username, password);
  await expect(refused.locator("body")).toContainText(/wyłączone|disabled|zablokowane/i);
  expect(new URL(refused.url()).origin).not.toBe(SITE);

  // 5. Unblock (fresh admin login) and the user gets in again with their password.
  await panel.getByRole("button", { name: "Odblokuj" }).click();
  await admin.locator("dialog[open]").getByRole("button", { name: "Odblokuj" }).click();
  await expect(admin.getByText("Konto odblokowane.")).toBeVisible();
  const again = await newPage(browser);
  await loginAs(again, username, password);

  // 6. Promote to expert: a confirmation names the role; the next request already has it.
  panel = await openUser(admin, username);
  await panel.getByLabel(/^Ekspert/).check();
  await panel.getByRole("button", { name: "Zapisz role" }).click();
  const confirm = admin.locator("dialog[open]");
  await expect(confirm).toContainText("Nadajesz rolę: Ekspert");
  await confirm.getByRole("button", { name: "Nadaj" }).click();
  await expect(admin.getByText("Role zapisane.")).toBeVisible();
  const me = await (await apiCall(again, "GET", "/me")).json();
  expect([...(me.roles as string[])].sort()).toEqual(["EXPERT", "MEMBER"]);
  const detail = await (await apiCall(admin, "GET", `/admin/users/${me.id}`)).json();
  // With MFA on, Keycloak asks the new expert to set up TOTP at the next login.
  if (mfaOff()) expect(detail.idp.required_actions).not.toContain("CONFIGURE_TOTP");
  else expect(detail.idp.required_actions).toContain("CONFIGURE_TOTP");

  // 7. Tidy up: the test user stays blocked.
  const id = detail.id as string;
  expect((await apiCall(admin, "POST", `/admin/users/${id}/block`, { reason: "other", note: "E2E cleanup" })).status()).toBe(200);
  const audit = await (await apiCall(admin, "GET", `/admin/audit-log?limit=50`)).json();
  const types = (audit.items as Array<{ event_type: string; target_id: string | null }>)
    .filter((e) => e.target_id === id)
    .map((e) => e.event_type);
  for (const event of ["USER_CREATED", "USER_PASSWORD_CHANGE_REQUIRED", "USER_BLOCKED", "USER_UNBLOCKED", "USER_ROLES_CHANGED"]) {
    expect(types).toContain(event);
  }
  expect(JSON.stringify(audit)).not.toContain(temporary);
});
