/**
 * Scenario 15 (US-07): an administrator brands the cooperative. admin1 sets the name, the two
 * colours and a logo in „Wygląd”; member1 then sees them in the menu, the tab title, the
 * favicon and the page's colours, with no CSP violation; an SVG logo is refused; a member cannot
 * change anything; „Przywróć domyślne” brings the original look back for everyone.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { apiCall, login, session, watchCsp } from "./support/stack";

const NAME = `Pasieka E2E ${Date.now().toString(36)}`;
const BRAND = "#1f6f4a";
// 240x60 PNG: a green rounded square and an orange bar on transparency.
const LOGO = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAPAAAAA8CAYAAABYfzddAAABGUlEQVR42u3csQ2CYBSF0R9j6Q5sYmLLMszCMrQmbsIO9LiAiZgY5b13Tm0hyJdLgqE1AOD3uk8+3I/DdrQDWKa58zMi4GDhChneBBwhXCFT2SlTvJG/N3wt4OgRiJiyAWe5+EVM6VtoIFjA2VbLCmOBgeMHnHWtrDAWGBAwIGBAwCBgQMCAgEHAQBRnp+C1x+3iDyD81fW+dhYY3EIDAgYEDAgYBAwIGBAwCBgQMCBgQMAgYEDAgIBBwE4BCBgQMCBgEDDQvNSupX2hGFhgQMAgYEDAgIABAUOBgJdpTvnYJOtxgQWGTAFnWyvriwUG4gScZbWsL2UXOPrFL17K30JHjUC8VLLrYu/HYRMuBA34yCELFwAAgJ2efI5CYA8dQmYAAAAASUVORK5CYII=",
  "base64",
);

test.describe.configure({ mode: "serial" });
test.beforeEach(() => test.skip(test.info().project.name !== "chromium", "desktop only"));

/** Serious or critical axe findings, with where they are. */
async function axe(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  return results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(" ")} ${n.failureSummary?.split("\n").pop() ?? ""}`));
}

/** The page's own value of a CSS custom property. */
const token = (page: Page, name: string) =>
  page.evaluate((n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(), name);

/** The layout caches the branding for 10 s (stale while revalidating): reload until it shows. */
async function reloadUntil(page: Page, check: () => Promise<boolean>): Promise<void> {
  await expect
    .poll(
      async () => {
        await page.reload();
        return check();
      },
      { timeout: 45_000, intervals: [1_000, 3_000, 5_000] },
    )
    .toBe(true);
}

test("admin1 brands the cooperative and member1 sees it", async ({ browser }) => {
  const admin = await session(browser, "admin1", "/");
  try {
    await admin.getByRole("navigation", { name: "Główna" }).getByRole("link", { name: "Wygląd" }).click();
    await expect(admin.getByRole("heading", { level: 1, name: "Wygląd" })).toBeVisible();

    await admin.getByLabel("Nazwa organizacji").fill(NAME);
    await admin.getByRole("checkbox", { name: "Własne kolory" }).check();
    await admin.getByLabel("Kolor marki", { exact: true }).fill(BRAND);
    await admin.getByLabel("Kolor akcentu", { exact: true }).fill("#c2410c");
    // The server's preview: the accent is adjusted for readable text.
    await expect(admin.getByText("Kolor akcentu dostosowany, aby tekst był czytelny.")).toBeVisible();
    await expect(admin.getByTestId("preview-light")).toContainText(NAME);
    await admin.getByRole("button", { name: "Zapisz" }).click();
    await expect(admin.getByText(/Zapisano\. Członkowie zobaczą zmiany/)).toBeVisible();

    // An SVG is refused whatever it claims; a PNG is accepted and re-encoded.
    await admin.getByLabel("Plik logo").setInputFiles({
      name: "logo.svg",
      mimeType: "image/svg+xml",
      buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    });
    await expect(admin.getByText(/SVG nie jest obsługiwany/)).toBeVisible();
    await admin.getByLabel("Plik logo").setInputFiles({ name: "logo.png", mimeType: "image/png", buffer: LOGO });
    await expect(admin.getByRole("img", { name: `Logo: ${NAME}` })).toBeVisible();
    // axe on the console itself.
    expect(await axe(admin)).toEqual([]);

    const member = await session(browser, "member1", "/wiki");
    const violations = await watchCsp(member);
    const nav = member.getByRole("navigation", { name: "Główna" });
    await reloadUntil(member, async () => (await member.locator("aside[data-sidebar]").first().textContent())?.includes(NAME) ?? false);
    await expect(member).toHaveTitle(`Baza wiedzy · ${NAME}`);
    expect(await token(member, "--primary")).toBe(BRAND);
    const logo = member.locator("aside[data-sidebar] img").last();
    await expect(logo).toHaveAttribute("src", /^\/api\/branding\/files\/\d+\/logo\.png$/);
    expect(await logo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await expect(member.locator('link[rel="icon"][type="image/png"]')).toHaveAttribute("href", /favicon-32\.png$/);
    await expect(nav.getByRole("link", { name: "Wiki" })).toHaveAttribute("aria-current", "page");
    expect(await violations()).toEqual([]);
    expect(await axe(member)).toEqual([]);

    // A member reads the branding but cannot change it.
    expect((await apiCall(member, "GET", "/branding")).status()).toBe(200);
    expect((await apiCall(member, "GET", "/admin/branding")).status()).toBe(403);
    expect((await apiCall(member, "POST", "/admin/branding/reset")).status()).toBe(403);
    await member.goto("/admin/branding");
    await expect(member.getByRole("heading", { name: "Brak dostępu" })).toBeVisible();
    await member.context().close();
  } finally {
    // Back to the default look for every other scenario.
    await admin.goto("/admin/branding");
    await admin.getByRole("button", { name: "Przywróć domyślne" }).click();
    await admin.getByRole("dialog").getByRole("button", { name: "Przywróć" }).click();
    await expect(admin.getByText("Przywrócono domyślny wygląd.")).toBeVisible();
  }
  const visitor = await login(await (await browser.newContext()).newPage(), "member2", "/");
  await reloadUntil(visitor, async () => !((await visitor.locator("aside[data-sidebar]").first().textContent())?.includes(NAME) ?? true));
  expect(await token(visitor, "--primary")).toBe("#9a6414");
  await admin.context().close();
});
