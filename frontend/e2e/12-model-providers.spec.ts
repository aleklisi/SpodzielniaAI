/**
 * Scenario 12 (Stage 10, US-04): every model over a configurable API.
 *
 * Starting with no vision model, the feature is off and a member's photo waits („Czeka na model
 * AI”) instead of failing; an admin adds the local server (LM Studio/Ollama) as a new provider,
 * clears it for private data, routes the vision purpose to it, and the waiting photo completes on
 * its own. Members see where their data is processed. The admin then rebuilds the text index: a
 * new version is built in the background and swapped in. Finally the starting configuration is
 * restored.
 */

import { expect, test, type Page } from "@playwright/test";

import { apiCall, secret, seed, session } from "./support/stack";

interface Revision {
  number: number;
  status: string;
  routes: Record<string, { name: string; kind: string } | undefined> | null;
}

interface Generation {
  number: number;
  status: string;
}

async function activeRevision(page: Page): Promise<Revision> {
  const revisions = (await (await apiCall(page, "GET", "/admin/llm/revisions")).json()) as Revision[];
  const active = revisions.find((r) => r.status === "ACTIVE");
  expect(active).toBeDefined();
  return active as Revision;
}

async function activateDraft(admin: Page, change: (admin: Page) => Promise<void>): Promise<void> {
  await admin.getByRole("tab", { name: "Przypisania" }).click();
  const leftover = admin.getByRole("button", { name: "Odrzuć szkic" });
  if (await leftover.isVisible()) await leftover.click();
  await admin.getByRole("button", { name: "Nowy szkic zmian" }).click();
  await change(admin);
  await admin.getByRole("button", { name: /^Aktywuj rewizję/ }).click();
  await admin.locator("dialog[open]").getByRole("button", { name: "Aktywuj" }).click();
  await expect(admin.getByRole("status").filter({ hasText: /jest aktywna/ })).toBeVisible();
}

async function derivativeStatus(member: Page, noteId: string): Promise<string | undefined> {
  const note = (await (await apiCall(member, "GET", `/notes/${noteId}`)).json()) as {
    derivatives: Array<{ kind: string; status: string }>;
  };
  return note.derivatives.find((d) => d.kind === "IMAGE_DESCRIPTION")?.status;
}

test("a missing model turns a feature off, work waits, and resumes once an admin configures one", async ({ browser }) => {
  test.setTimeout(1_800_000);
  const admin = await session(browser, "admin1", "/admin/llm");
  await expect(admin.getByRole("heading", { name: "Modele AI" })).toBeVisible();
  const before = await activeRevision(admin);
  const vlm = before.routes?.VLM;
  test.skip(!vlm, "the stack has no vision model configured (LLM_VLM_MODEL)");
  const vlmModel = (vlm?.name ?? "").split("/").slice(1).join("/");
  const local = ((await (await apiCall(admin, "GET", "/admin/llm/status")).json()) as { local_endpoints: string[] })
    .local_endpoints[0];
  const provider = `local-e2e-${Date.now().toString(36)}`;

  try {
    // 1. No vision model: the console lists the purpose as missing; the feature is off.
    await activateDraft(admin, async (page) => {
      await page.getByLabel("Model dla: Opisy zdjęć").selectOption("");
    });
    await expect(admin.getByRole("region", { name: "Brakujące modele" })).toContainText("Opisy zdjęć");

    const member = await session(browser, "member1", "/notebook");
    const capabilities = (await (await apiCall(member, "GET", "/capabilities")).json()) as {
      features: Record<string, boolean>;
    };
    expect(capabilities.features.image_descriptions).toBe(false);

    // 2. The member's photo is accepted and waits instead of failing.
    await member.getByRole("button", { name: "Nowa notatka" }).click();
    await member.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
    const noteId = member.url().split("/").pop() ?? "";
    const { jpeg } = seed<{ jpeg: string }>({ action: "gps_jpeg" });
    const upload = member.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
    await member.getByLabel("Wybierz pliki do wgrania").setInputFiles({
      name: "ramka.jpg",
      mimeType: "image/jpeg",
      buffer: Buffer.from(jpeg, "base64"),
    });
    expect((await upload).status()).toBe(202);
    await expect.poll(() => derivativeStatus(member, noteId), { timeout: 120_000 }).toBe("WAITING_FOR_MODEL");
    await member.reload();
    await expect(member.getByText("Czeka na model AI").first()).toBeVisible();

    // 3. The admin adds the local server as a provider and clears it for private data.
    await admin.getByRole("tab", { name: "Dostawcy" }).click();
    await admin.getByRole("button", { name: "Dodaj dostawcę" }).click();
    const dialog = admin.locator("dialog[open]");
    await dialog.getByLabel(/^Nazwa/).fill(provider);
    await dialog.getByLabel(/Adres API/).fill(`${local}/v1`);
    const token = secret("llm_api_key");
    if (token) await dialog.getByLabel(/Klucz API/).fill(token);
    await dialog.getByRole("button", { name: "Zapisz" }).click();
    const providerRow = admin.getByRole("row", { name: new RegExp(provider) });
    await providerRow.getByRole("button", { name: "Dopuść do danych prywatnych" }).click();
    const clearance = admin.locator("dialog[open]");
    await clearance.getByLabel("Region przetwarzania").fill("serwer spółdzielni");
    await clearance.getByLabel("Przechowywanie danych przez dostawcę").fill("brak");
    await clearance.getByLabel(/Umowa/).fill("E2E scenariusz 12");
    await clearance.getByRole("button", { name: "Dopuść" }).click();
    await expect(providerRow.getByText("Dopuszczony do danych prywatnych")).toBeVisible();

    // 4. Its vision model, probed for real, then routed to the vision purpose.
    await admin.getByRole("tab", { name: "Modele" }).click();
    const form = admin.getByRole("form", { name: "Dodaj model" });
    await form.getByLabel("Dostawca").selectOption({ label: `${provider} (lokalny)` });
    await form.getByLabel("Nazwa modelu na serwerze").fill(vlmModel);
    await form.getByLabel("Typ").selectOption("VLM");
    await form.getByRole("button", { name: "Dodaj" }).click();
    const row = admin.getByRole("row", { name: new RegExp(`${provider}/`) });
    await row.getByRole("button", { name: "Testuj model" }).click();
    await expect(row.getByText("Zaliczony", { exact: true })).toBeVisible({ timeout: 600_000 });
    await activateDraft(admin, async (page) => {
      await page.getByLabel("Model dla: Opisy zdjęć").selectOption({ label: `${provider}/${vlmModel}` });
    });
    await expect(admin.getByRole("region", { name: "Brakujące modele" })).toHaveCount(0);

    // 5. The waiting photo completes on its own (the configuration change resumes it).
    await expect.poll(() => derivativeStatus(member, noteId), { timeout: 600_000, intervals: [3_000] }).toBe("DONE");

    // 6. Members see where their data is processed.
    await member.goto("/privacy");
    await expect(member.getByRole("heading", { name: "Gdzie przetwarzane są Twoje dane" })).toBeVisible();
    const card = member.getByLabel(`Dostawca ${provider}`);
    await expect(card).toContainText("Serwer spółdzielni");
    await expect(card).toContainText("E2E scenariusz 12");
    await expect(card).toContainText("opisy zdjęć");

    // 7. Rebuilding the text index: a new version is built in the background, then swapped in.
    await admin.getByRole("tab", { name: "Indeksy" }).click();
    const index = async () =>
      (await (await apiCall(admin, "GET", "/admin/llm/index")).json()) as { generations: Generation[] };
    const serving = (await index()).generations.find((g) => g.status === "SERVING");
    await admin.getByRole("button", { name: "Przebuduj indeks" }).click();
    await expect(admin.getByText("W budowie").first()).toBeVisible();
    await expect
      .poll(async () => (await index()).generations.find((g) => g.status === "SERVING")?.number, {
        timeout: 1_200_000,
        intervals: [5_000],
      })
      .toBeGreaterThan(serving?.number ?? 0);
  } finally {
    // 8. Back to the configuration we started from; e2e providers are disabled.
    await admin.goto("/admin/llm");
    await admin.getByRole("tab", { name: "Historia" }).click();
    const item = admin.getByRole("listitem").filter({ has: admin.getByText(`Rewizja ${before.number}`, { exact: true }) });
    await item.getByRole("button", { name: "Przywróć" }).click();
    await admin.locator("dialog[open]").getByRole("button", { name: "Przywróć" }).click();
    await expect(admin.getByRole("status").filter({ hasText: /Przywrócono konfigurację/ })).toBeVisible();
    const providers = (await (await apiCall(admin, "GET", "/admin/llm/providers")).json()) as Array<{
      id: string;
      name: string;
      disabled: boolean;
    }>;
    for (const p of providers.filter((p) => p.name.startsWith("local-e2e-") && !p.disabled)) {
      await apiCall(admin, "POST", `/admin/llm/providers/${p.id}/disabled`, { disabled: true });
    }
  }
});
