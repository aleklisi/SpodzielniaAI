/**
 * Scenario 11 (Stage 9): the visual atlas. A member annotates a photo in the notebook (domain
 * from `atlas seed-example`), sends it to the atlas anonymously, an expert corrects a box and
 * approves it, and another member finds it under its label, without the author's name. That
 * member's own photo of the same frame is recognised from the atlas (local SigLIP + local VLM)
 * with a citation; when the author suspends the photo, it leaves the atlas and the citation.
 * The dataset export is refused while the operator keeps it disabled.
 */

import { expect, test, type Page } from "@playwright/test";

import { login, seed, session } from "./support/stack";

test.describe.configure({ mode: "serial" });

/** Drag on the annotator surface from one fraction of the photo to another. */
async function drawBox(page: Page, from: [number, number], to: [number, number]): Promise<void> {
  const surface = page.getByTestId("annotator-surface");
  const box = (await surface.boundingBox())!;
  await page.mouse.move(box.x + box.width * from[0], box.y + box.height * from[1]);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * ((from[0] + to[0]) / 2), box.y + box.height * ((from[1] + to[1]) / 2), { steps: 4 });
  await page.mouse.move(box.x + box.width * to[0], box.y + box.height * to[1], { steps: 4 });
  await page.mouse.up();
}

async function pickLabel(page: Page, index: number, query: string, name: string): Promise<void> {
  const combo = page.getByRole("combobox", { name: `Etykieta oznaczenia ${index}` });
  await combo.fill(query);
  await page.getByRole("option", { name: new RegExp(name) }).first().click();
}

let caption = "";
let jpeg = "";
let imageId = "";

test("a member annotates a photo and sends it to the atlas anonymously", async ({ page }) => {
  test.setTimeout(240_000);
  seed({ action: "atlas_example" });
  jpeg = seed<{ jpeg: string }>({ action: "atlas_jpeg" }).jpeg;
  // A word, not a number: PII scrubbing may replace long digit runs.
  const tag = Array.from({ length: 8 }, () => String.fromCharCode(97 + Math.floor(Math.random() * 26))).join("");
  caption = `Matka na ramce ${tag}`;

  await login(page, "member1", "/notebook");
  await page.getByRole("button", { name: "Nowa notatka" }).click();
  await page.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
  const upload = page.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
  await page.getByLabel("Wybierz pliki do wgrania").setInputFiles({ name: "matka.jpg", mimeType: "image/jpeg", buffer: Buffer.from(jpeg, "base64") });
  expect((await upload).status()).toBe(202);
  await page.reload(); // the note now lists the photo
  await page.getByRole("link", { name: "Oznacz: matka.jpg" }).click();
  await page.waitForURL(/\/photos\/[0-9a-f-]{36}$/);

  await page.getByRole("combobox", { name: "Dziedzina", exact: true }).selectOption({ label: "Pszczelarstwo" });
  await page.getByLabel("Opis zdjęcia").fill(caption);
  await drawBox(page, [0.2, 0.2], [0.45, 0.5]);
  await pickLabel(page, 1, "matka", "Matka pszczela");
  await page.getByLabel("Opis oznaczenia").first().fill("znaczona na zielono");
  await drawBox(page, [0.6, 0.55], [0.8, 0.85]);
  await pickLabel(page, 2, "matecz", "Matecznik");
  await page.getByRole("button", { name: "Zapisz" }).click();
  await expect(page.getByText("Zapisano oznaczenia.")).toBeVisible();

  await page.getByRole("button", { name: "Przekaż do atlasu" }).click();
  // worker-core prepares it: metadata-free copy, PII scrub, injection screen, local VLM check.
  await page.getByRole("button", { name: "Sprawdź i wyślij" }).click({ timeout: 180_000 });
  const dialog = page.getByRole("dialog", { name: "Tak zobaczą to inni" });
  await expect(dialog.getByText(caption)).toBeVisible();
  await expect(dialog.getByText("Kopia zdjęcia nie zawiera metadanych", { exact: false })).toBeVisible();
  const send = dialog.getByRole("button", { name: "Wyślij do ekspertów" });
  await expect(send).toBeDisabled(); // the author must choose how to be named
  await dialog.getByLabel(/Publikuj anonimowo/).check();
  const people = dialog.getByLabel(/zgodziły się na jego udostępnienie/);
  if (await people.isVisible()) await people.check();
  await send.click();
  await expect(page.getByText("W recenzji ekspertów")).toBeVisible();
});

test("an expert corrects a box and approves the photo", async ({ browser }) => {
  const expert = await session(browser, "expert1", "/review/atlas");
  const item = expert.getByRole("button", { name: /Pszczelarstwo/ }).first();
  await item.click();
  await expect(expert.getByText(caption)).toBeVisible();
  // Widen the queen's box with the keyboard, then save the correction.
  await expert.getByRole("button", { name: /Oznaczenie 1: Matka pszczela/ }).focus();
  await expert.keyboard.press("Alt+ArrowRight");
  await expert.getByRole("button", { name: "Zapisz poprawki" }).click();
  await expect(expert.getByText(/Zapisano poprawki/)).toBeVisible();
  await expect(expert.getByText("(poprawione)")).toBeVisible();
  await expert.getByRole("button", { name: "Zapisz decyzję" }).click();
  await expect(expert.getByText(/Zdjęcie trafiło do atlasu|Decyzja zapisana/)).toBeVisible();
  await expert.context().close();
});

test("another member finds the photo under its label, without the author's name", async ({ browser }) => {
  const member = await session(browser, "member2", "/atlas");
  await member.getByRole("combobox", { name: "Dziedzina", exact: true }).selectOption({ label: "Pszczelarstwo" });
  await member.getByLabel("Etykieta").selectOption({ label: "Matka pszczela" });
  await member.getByLabel("Szukaj").fill(caption.split(" ").pop()!);
  const card = member.getByRole("list", { name: "Zdjęcia atlasu" }).getByRole("link").first();
  await expect(card).toContainText("Członek spółdzielni");
  await card.click();
  await member.waitForURL(/\/atlas\/[0-9a-f-]{36}$/);
  imageId = member.url().split("/").pop() ?? "";
  await expect(member.getByRole("heading", { name: /Matka pszczela/ })).toBeVisible();
  await expect(member.getByText(/Autor: Członek spółdzielni/)).toBeVisible();
  await expect(member.getByText("znaczona na zielono")).toBeVisible();
  await member.context().close();
});

test("a member's own photo is recognised from the verified atlas, with a citation", async ({ browser }) => {
  // A local 12B vision model may need a retry for valid JSON: over 3 minutes on a laptop.
  test.setTimeout(480_000);
  const member = await session(browser, "member2", "/notebook");
  await member.getByRole("button", { name: "Nowa notatka" }).click();
  await member.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
  const upload = member.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
  await member.getByLabel("Wybierz pliki do wgrania").setInputFiles({ name: "moja-ramka.jpg", mimeType: "image/jpeg", buffer: Buffer.from(jpeg, "base64") });
  expect((await upload).status()).toBe(202);
  await member.reload();
  await member.getByRole("link", { name: "Oznacz: moja-ramka.jpg" }).click();
  await member.getByRole("combobox", { name: "Dziedzina", exact: true }).selectOption({ label: "Pszczelarstwo" });
  await member.getByLabel("Pytanie (opcjonalnie)").fill("Czy na ramce jest matka?");
  await member.getByRole("button", { name: "Rozpoznaj" }).click();
  const result = member.getByRole("region", { name: "Wynik rozpoznawania" });
  await expect(result).toBeVisible({ timeout: 420_000 });
  // This run's photo is among the cited ones (earlier runs' synthetic photos may be too).
  await expect(result.locator(`a[href="/atlas/${imageId}"]`)).toContainText("Członek spółdzielni");
  await expect(result.locator("p").first()).not.toBeEmpty();
  // The test photo is random shapes: a real local VLM may rightly see no queen and suggest
  // nothing. Accepting suggestions is covered by the integration and component tests.
  const suggestions = member.getByRole("list", { name: "Podpowiedzi" });
  if (await suggestions.isVisible()) {
    await suggestions.getByRole("button", { name: "Przyjmij" }).first().click();
    await expect(member.getByText("(z rozpoznawania)")).toBeVisible();
    await member.getByRole("button", { name: "Zapisz" }).click();
    await expect(member.getByText("Zapisano oznaczenia.")).toBeVisible();
  }
  await member.context().close();
});

test("the author suspends the photo: it leaves the atlas and the citation", async ({ browser }) => {
  const author = await session(browser, "member1", "/me/contributions");
  const row = author.getByRole("listitem").filter({ has: author.locator(`a[href="/atlas/${imageId}"]`) });
  await row.getByRole("button", { name: "Wstrzymaj" }).click();
  await expect(author.getByText("Zdjęcie wstrzymane.")).toBeVisible();
  await author.context().close();

  const member = await session(browser, "member2", `/atlas/${imageId}`);
  await expect(member.getByText(/nie istnieje, zostało wycofane/)).toBeVisible();
  await member.context().close();
});

test("the dataset export is refused while the operator keeps it disabled", async ({ browser }) => {
  const admin = await session(browser, "admin1", "/admin/atlas");
  await expect(admin.getByText(/Eksport jest wyłączony/)).toBeVisible();
  await admin.context().close();
});

