/** Scenario 1: authentication and roles (OIDC via Keycloak, BFF session, MFA for experts). */

import { expect, test } from "@playwright/test";

import { apiCall, login } from "./support/stack";

test("a member is kept out of the review panel, in the UI and the API", async ({ page }) => {
  await login(page, "member1", "/review");
  await expect(page.getByRole("heading", { name: "Brak dostępu" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Recenzje" })).toHaveCount(0);
  expect((await apiCall(page, "GET", "/reviews/queue")).status()).toBe(403);
});

test("an expert who logged in without a second factor is sent to step up", async ({ page }) => {
  await login(page, "expert2", "/review");
  await expect(page.getByRole("heading", { name: "Potwierdź swoją tożsamość" })).toBeVisible();
  const link = page.getByRole("link", { name: "Zaloguj ponownie z MFA" });
  await expect(link).toHaveAttribute("href", /\/api\/auth\/login\?prompt=login.*acr_values=/);
  const queue = await apiCall(page, "GET", "/reviews/queue");
  expect(queue.status()).toBe(403);
  expect((await queue.json()).detail.error).toBe("mfa_required");
});

test("an expert with OTP reaches the review queue", async ({ page }) => {
  await login(page, "expert1", "/review");
  await expect(page.getByRole("heading", { name: "Recenzje" })).toBeVisible();
  expect((await apiCall(page, "GET", "/reviews/queue")).status()).toBe(200);
});
