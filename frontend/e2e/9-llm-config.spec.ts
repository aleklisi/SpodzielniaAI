/**
 * Scenario 9 (Stage 7, US-01): an admin changes which model answers the cooperative chat,
 * through the UI, without restarting anything, and rolls the change back.
 *
 * Uses the stack's real local model: a second provider on the same local endpoint
 * (`local-e2e-…`) serves the same chat model with temperature 0, so the scenario works with
 * whatever LM Studio/Ollama model the stack is configured with. The probe is a real call.
 */

import { expect, test, type Page } from "@playwright/test";

import { apiCall, secret, session } from "./support/stack";

interface Status {
  active_revision: number;
  process_revision: number;
  local_endpoints: string[];
}

interface Revision {
  number: number;
  status: string;
  routes: Record<string, { name: string; kind: string } | undefined> | null;
}

async function status(page: Page): Promise<Status> {
  const response = await apiCall(page, "GET", "/admin/llm/status");
  expect(response.status()).toBe(200);
  return (await response.json()) as Status;
}

async function activeRevision(page: Page): Promise<Revision> {
  const revisions = (await (await apiCall(page, "GET", "/admin/llm/revisions")).json()) as Revision[];
  const active = revisions.find((r) => r.status === "ACTIVE");
  expect(active).toBeDefined();
  return active as Revision;
}

test("members cannot see or call the model console", async ({ browser }) => {
  const member = await session(browser, "member1", "/admin/llm");
  await expect(member.getByRole("heading", { name: "Brak dostępu" })).toBeVisible();
  expect((await apiCall(member, "GET", "/admin/llm/status")).status()).toBe(403);
  expect((await apiCall(member, "POST", "/admin/llm/playground", { model_ids: [], prompt: "x" })).status()).toBe(403);
});

test("an admin switches the chat model, the API follows without a restart, and rolls back", async ({ browser }) => {
  test.setTimeout(900_000);
  const admin = await session(browser, "admin1", "/admin/llm");
  await expect(admin.getByRole("heading", { name: "Modele AI" })).toBeVisible();

  const before = await activeRevision(admin);
  const rag = before.routes?.RAG;
  test.skip(!rag, "the stack has no chat model configured (LLM_CHAT_MODEL)");
  // The test re-adds the chat model on the stack's local endpoint: it must be served there.
  test.skip(rag?.kind !== "LOCAL", "the chat model is not on a local endpoint (e.g. a cloud API)");
  const chatModel = (rag?.name ?? "").split("/").slice(1).join("/");
  const local = (await status(admin)).local_endpoints[0];
  const provider = `local-e2e-${Date.now().toString(36)}`;

  // 1. A provider on the stack's local endpoint (the UI explains it is local).
  await admin.getByRole("tab", { name: "Dostawcy" }).click();
  await admin.getByRole("button", { name: "Dodaj dostawcę" }).click();
  const dialog = admin.locator("dialog[open]");
  await dialog.getByLabel(/^Nazwa/).fill(provider);
  await dialog.getByLabel(/Adres API/).fill(`${local}/v1`);
  await expect(dialog.getByText(/Serwer lokalny/)).toBeVisible();
  const token = secret("llm_api_key"); // LM Studio wants one; Ollama ignores it
  if (token) await dialog.getByLabel(/Klucz API/).fill(token);
  await dialog.getByRole("button", { name: "Zapisz" }).click();
  const providerRow = admin.getByRole("row", { name: new RegExp(provider) });
  await expect(providerRow).toBeVisible();

  // 1b. Cleared for private data (10.1): a new provider gets none until an admin says so.
  await providerRow.getByRole("button", { name: "Dopuść do danych prywatnych" }).click();
  const clearance = admin.locator("dialog[open]");
  await clearance.getByLabel("Region przetwarzania").fill("serwer spółdzielni");
  await clearance.getByLabel("Przechowywanie danych przez dostawcę").fill("brak");
  await clearance.getByLabel(/Umowa/).fill("E2E");
  await clearance.getByRole("button", { name: "Dopuść" }).click();
  await expect(providerRow.getByText("Dopuszczony do danych prywatnych")).toBeVisible();

  // 2. The same chat model on it, deterministic, then a real probe.
  await admin.getByRole("tab", { name: "Modele" }).click();
  const form = admin.getByRole("form", { name: "Dodaj model" });
  await form.getByLabel("Dostawca").selectOption({ label: `${provider} (lokalny)` });
  await form.getByLabel("Nazwa modelu na serwerze").fill(chatModel);
  await form.getByLabel("Temperatura").fill("0");
  await form.getByRole("button", { name: "Dodaj" }).click();
  const row = admin.getByRole("row", { name: new RegExp(`${provider}/`) });
  await row.getByRole("button", { name: "Testuj model" }).click();
  await expect(row.getByText("Zaliczony", { exact: true })).toBeVisible({ timeout: 600_000 });

  // 3. A draft routes the chat to it; activation shows the change and goes live.
  await admin.getByRole("tab", { name: "Przypisania" }).click();
  const leftover = admin.getByRole("button", { name: "Odrzuć szkic" }); // from an aborted run
  if (await leftover.isVisible()) await leftover.click();
  await admin.getByRole("button", { name: "Nowy szkic zmian" }).click();
  const select = admin.getByLabel("Model dla: Czat spółdzielni (RAG)");
  await select.selectOption({ label: `${provider}/${chatModel}` });
  await expect(admin.getByLabel("Model dla: Wnioski do Wiki").locator("option", { hasText: provider })).toHaveCount(1);
  await admin.getByRole("button", { name: /^Aktywuj rewizję/ }).click();
  const confirm = admin.locator("dialog[open]");
  await expect(confirm.getByRole("list", { name: "Zmiany" })).toContainText(`${provider}/${chatModel}`);
  await confirm.getByRole("button", { name: "Aktywuj" }).click();
  await expect(admin.getByRole("status").filter({ hasText: /jest aktywna/ })).toBeVisible();

  const switched = await status(admin);
  expect(switched.active_revision).toBeGreaterThan(before.number);
  expect(switched.process_revision).toBe(switched.active_revision); // no restart needed
  expect((await activeRevision(admin)).routes?.RAG?.name).toBe(`${provider}/${chatModel}`);

  // 4. Roll back to the revision we started from.
  await admin.getByRole("tab", { name: "Historia" }).click();
  const item = admin.getByRole("listitem").filter({ has: admin.getByText(`Rewizja ${before.number}`, { exact: true }) });
  await item.getByRole("button", { name: "Przywróć" }).click();
  await admin.locator("dialog[open]").getByRole("button", { name: "Przywróć" }).click();
  await expect(admin.getByRole("status").filter({ hasText: /Przywrócono konfigurację/ })).toBeVisible();
  const restored = await activeRevision(admin);
  expect(restored.routes?.RAG?.name).toBe(rag?.name);
  expect((await status(admin)).process_revision).toBe(restored.number);

  // 5. Tidy up: e2e providers stay in history (referenced by revisions) but are disabled.
  const providers = (await (await apiCall(admin, "GET", "/admin/llm/providers")).json()) as Array<{ id: string; name: string; disabled: boolean }>;
  for (const p of providers.filter((p) => p.name.startsWith("local-e2e-") && !p.disabled)) {
    expect((await apiCall(admin, "POST", `/admin/llm/providers/${p.id}/disabled`, { disabled: true })).status()).toBe(200);
  }
});
