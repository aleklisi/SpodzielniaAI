/**
 * Scenario 13 (11.1, US-05): an administrator removes a model provider nobody uses.
 *
 * Given admin1 is logged in, when they see the list of LLM providers, then they can remove a
 * provider and get the refreshed list. A provider the running configuration needs keeps a
 * disabled „Usuń” with the reason next to it.
 */

import { expect, test } from "@playwright/test";

import { apiCall, session } from "./support/stack";

interface ProviderRow {
  name: string;
  in_active: boolean;
  removal_blocked: string | null;
}

test("an admin removes an unused provider and sees the refreshed list", async ({ browser }) => {
  const admin = await session(browser, "admin1", "/admin/llm");
  await expect(admin.getByRole("heading", { name: "Modele AI" })).toBeVisible();
  const local = ((await (await apiCall(admin, "GET", "/admin/llm/status")).json()) as { local_endpoints: string[] })
    .local_endpoints[0];
  const name = `do-usuniecia-${Date.now().toString(36)}`;

  // A provider to remove, added through the console like any other.
  await admin.getByRole("tab", { name: "Dostawcy" }).click();
  await admin.getByRole("button", { name: "Dodaj dostawcę" }).click();
  const form = admin.locator("dialog[open]");
  await form.getByLabel(/^Nazwa/).fill(name);
  await form.getByLabel(/Adres API/).fill(`${local}/v1`);
  await form.getByRole("button", { name: "Zapisz" }).click();
  const row = admin.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();

  // A provider the active configuration uses cannot be removed, and says why.
  const providers = (await (await apiCall(admin, "GET", "/admin/llm/providers")).json()) as ProviderRow[];
  const inUse = providers.find((p) => p.in_active);
  expect(inUse?.removal_blocked).toBe("provider_in_use");
  const inUseRow = admin.getByRole("row", { name: new RegExp(`^${inUse?.name ?? ""}\\b`) }).first();
  await expect(inUseRow.getByRole("button", { name: `Usuń dostawcę ${inUse?.name ?? ""}` })).toBeDisabled();
  await expect(inUseRow.getByText("Używany przez aktywną konfigurację")).toBeVisible();

  // Remove: confirm, the list refreshes without it.
  await row.getByRole("button", { name: `Usuń dostawcę ${name}` }).click();
  const dialog = admin.locator("dialog[open]");
  await expect(dialog).toContainText("zostanie usunięty całkowicie");
  await dialog.getByRole("button", { name: "Usuń" }).click();
  await expect(admin.getByText(`Dostawca „${name}” został usunięty.`)).toBeVisible();
  await expect(row).toHaveCount(0);
  await expect(inUseRow).toBeVisible();

  // The refreshed list is what the server has, also after a reload.
  await admin.reload();
  await admin.getByRole("tab", { name: "Dostawcy" }).click();
  await expect(admin.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
  const after = (await (await apiCall(admin, "GET", "/admin/llm/providers")).json()) as ProviderRow[];
  expect(after.map((p) => p.name)).not.toContain(name);
});
