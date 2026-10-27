/**
 * Scenario 16 (US-08): a member switches the UI to English. The page reloads on the same address
 * in English (menu, page, tab title, `<html lang>`, dates), the choice follows the account to
 * another browser from its first page, the chat's own texts are English, a signed-out visitor
 * can switch too, nothing breaks the CSP or axe, and Polish comes back with one click.
 * Desktop in `chromium`; the no-sideways-scroll check in English in `mobile`.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { apiCall, login, session, watchCsp } from "./support/stack";

const USER = "member2";
const project = () => test.info().project.name;

/** Serious or critical axe findings. */
async function axe(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
}

const lang = (page: Page) => page.locator("html").getAttribute("lang");
const menu = (page: Page) => page.locator("aside[data-sidebar]").first();

/** Choose a language in the desktop menu and wait for the reload into it. */
async function choose(page: Page, group: string, language: "English" | "Polski", expected: string): Promise<void> {
  await menu(page).getByRole("group", { name: group }).getByText(language, { exact: true }).click();
  await expect.poll(() => lang(page), { timeout: 20_000 }).toBe(expected);
}

test.describe("desktop", () => {
  test.beforeEach(() => test.skip(project() !== "chromium", "desktop only"));

  test("member2 switches to English, keeps it on another browser, and switches back", async ({ browser }) => {
    const page = await session(browser, USER, "/wiki");
    const violations = await watchCsp(page);
    try {
      await page.reload();
      expect(await lang(page)).toBe("pl");
      await expect(page.getByRole("heading", { level: 1, name: "Baza wiedzy" })).toBeVisible();

      await choose(page, "Język", "English", "en-GB");
      expect(new URL(page.url()).pathname).toBe("/wiki"); // the same page, now in English
      await expect(page.getByRole("heading", { level: 1, name: "Knowledge base" })).toBeVisible();
      await expect(page).toHaveTitle(/^Knowledge base · /);
      const nav = page.getByRole("navigation", { name: "Main" });
      await expect(nav.getByRole("link", { name: "Notebook" })).toBeVisible();
      await expect(nav.getByRole("link", { name: "Ask the cooperative" })).toBeVisible();
      await expect(menu(page).getByRole("group", { name: "Language" }).getByRole("radio", { name: "English" })).toBeChecked();
      expect((await (await apiCall(page, "GET", "/me")).json()).locale).toBe("en");

      // Dates follow the language: a new note's time reads "27 Oct 2026, 09:12".
      await page.goto("/notebook");
      await page.getByRole("button", { name: "New note" }).click();
      await page.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
      await expect(page.getByRole("region", { name: "Sources" })).toBeAttached();
      await page.goto("/notebook");
      await expect(page.getByRole("list", { name: "Notes" }).locator("time").first()).toHaveText(/^\d{1,2} [A-Z][a-z]{2} \d{4}, \d{2}:\d{2}$/);

      // The chat's chrome and the server's own texts are English.
      await page.goto("/chat");
      await expect(page.getByRole("heading", { level: 1, name: "Ask the cooperative" })).toBeVisible();
      await page.getByLabel("Your question").fill("How do I keep alpacas on a balcony in winter?");
      await page.getByRole("button", { name: "Ask" }).click();
      const outcome = page
        .getByText("No official position of the experts")
        .or(page.getByRole("list", { name: "Sources" }))
        .or(page.getByText(/No model is configured/))
        .or(page.getByText(/No answer could be obtained/));
      await expect(outcome.first()).toBeVisible({ timeout: 180_000 });
      await expect(page.getByLabel("Your question")).toHaveAttribute("placeholder", "E.g. how do I treat varroa in winter?");

      expect(await violations()).toEqual([]);
      expect(await axe(page)).toEqual([]);

      // Another browser signs in and is English from its first page.
      const other = await session(browser, USER, "/notebook");
      expect(await lang(other)).toBe("en-GB");
      await expect(other.getByRole("heading", { level: 1, name: "My notebook" })).toBeVisible();
      await other.context().close();

      await choose(page, "Language", "Polski", "pl");
      await expect(page.getByRole("heading", { level: 1, name: "Zapytaj spółdzielnię" })).toBeVisible();
    } finally {
      // Every other scenario expects member2 in Polish.
      await apiCall(page, "PUT", "/me/locale", { locale: "pl" });
      await page.context().close();
    }
  });

  test("a signed-out visitor switches with the cookie alone", async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("/");
    await expect(menu(page).getByRole("link", { name: "Zaloguj się" })).toBeVisible();
    await choose(page, "Język", "English", "en-GB");
    await expect(menu(page).getByRole("link", { name: "Sign in" })).toBeVisible();
    expect((await context.cookies()).find((c) => c.name === "lang")?.value).toBe("en");
    // A forged cookie value is Polish.
    await context.addCookies([{ name: "lang", value: "<script>", url: page.url() }]);
    await page.reload();
    expect(await lang(page)).toBe("pl");
    await context.close();
  });
});

test.describe("phone", () => {
  test.beforeEach(() => test.skip(project() !== "mobile", "mobile only"));

  test("no page scrolls sideways in English at phone width", async ({ page }) => {
    await login(page, USER, "/");
    try {
      expect((await apiCall(page, "PUT", "/me/locale", { locale: "en" })).status()).toBe(204);
      for (const path of ["/", "/notebook", "/wiki", "/atlas", "/chat", "/me/contributions", "/privacy"]) {
        await page.goto(path);
        expect(await lang(page)).toBe("en-GB");
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, path).toBeLessThanOrEqual(0);
      }
      // The drawer offers the language too.
      await page.getByRole("button", { name: "Open menu" }).click();
      await expect(page.getByRole("dialog", { name: "Menu" }).getByRole("group", { name: "Language" })).toBeVisible();
    } finally {
      await apiCall(page, "PUT", "/me/locale", { locale: "pl" });
    }
  });
});
