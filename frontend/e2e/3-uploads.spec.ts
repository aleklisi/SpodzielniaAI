/** Scenario 3: upload security (type by content, ClamAV, EXIF stripping). */

import { expect, test } from "@playwright/test";

import { email, login, seed } from "./support/stack";

async function newNote(page: import("@playwright/test").Page): Promise<string> {
  await login(page, "member1", "/notebook");
  await page.getByRole("button", { name: "Nowa notatka" }).click();
  await page.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
  return page.url().split("/").pop() ?? "";
}

test("an executable renamed to .pdf is refused (415) with an explanation", async ({ page }) => {
  await newNote(page);
  const upload = page.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
  await page.getByLabel("Wybierz pliki do wgrania").setInputFiles({
    name: "faktura.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.concat([Buffer.from("MZ\x90\x00"), Buffer.alloc(512)]),
  });
  expect((await upload).status()).toBe(415);
  const list = page.getByRole("list", { name: "Wgrywane pliki" });
  await expect(list.getByRole("alert")).toHaveText("Pliki wykonywalne są niedozwolone.");
});

test("a photo's GPS position is removed unless the member keeps it", async ({ page }) => {
  await newNote(page);
  const { jpeg } = seed<{ jpeg: string }>({ action: "gps_jpeg" });
  const results: Record<string, boolean> = {};
  for (const keep of [false, true]) {
    if (keep) await page.getByRole("checkbox", { name: /Zachowaj lokalizację GPS/ }).check();
    const upload = page.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
    await page.getByLabel("Wybierz pliki do wgrania").setInputFiles({
      name: keep ? "ul-z-gps.jpg" : "ul.jpg",
      mimeType: "image/jpeg",
      buffer: Buffer.from(jpeg, "base64"),
    });
    const response = await upload;
    expect(response.status()).toBe(202);
    const attachment = await response.json();
    results[String(keep)] = seed<{ has_gps: boolean }>({
      action: "gps",
      email: email("member1"),
      attachment_id: attachment.id,
    }).has_gps;
  }
  expect(results).toEqual({ false: false, true: true });
});
