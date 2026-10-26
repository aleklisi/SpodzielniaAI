/**
 * Scenario 14 (US-06): the Open Notebook–style shell. The menu on the left and the workspace on
 * the right on a desktop, an icon rail on a tablet, a drawer on a phone; the note's Źródła |
 * Notatka | Zapytaj columns become tabs; no page scrolls sideways; axe finds nothing serious.
 * Runs in the `chromium` (desktop), `tablet` and `mobile` projects; each test picks its own.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login, watchCsp } from "./support/stack";

const ROUTES: Record<string, string[]> = {
  member1: ["/", "/notebook", "/wiki", "/atlas", "/chat", "/me/drafts", "/me/contributions", "/privacy"],
  expert1: ["/review", "/review/atlas", "/review/labels"],
  admin1: ["/admin/security", "/admin/llm", "/admin/users", "/admin/atlas"],
};

const project = () => test.info().project.name;
const only = (...names: string[]) => test.skip(!names.includes(project()), `runs in: ${names.join(", ")}`);

/** The desktop menu (the drawer's copy is only in the DOM while it is open). */
const menu = (page: Page) => page.locator("aside[data-sidebar]").first();

async function newNote(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Nowa notatka" }).click();
  await page.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
}

test.describe("desktop", () => {
  test.beforeEach(() => only("chromium"));

  test("the menu is on the left and the workspace takes at least 2/3, scrolling on its own", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const violations = await watchCsp(page);
    await login(page, "member1", "/notebook");
    const nav = page.getByRole("navigation", { name: "Główna" });
    await expect(nav.getByRole("link", { name: "Notatnik" })).toHaveAttribute("aria-current", "page");
    const aside = await menu(page).boundingBox();
    const main = await page.getByRole("main").boundingBox();
    expect(aside!.x).toBe(0);
    expect(main!.x).toBeGreaterThanOrEqual(aside!.width);
    expect(main!.width / 1440).toBeGreaterThanOrEqual(2 / 3);
    // The document itself does not scroll: only the workspace does.
    expect(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight)).toBe(true);
    await expect(page.getByRole("main")).toHaveCSS("overflow-y", "auto");
    await nav.getByRole("link", { name: "Wiki" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Baza wiedzy" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Wiki" })).toHaveAttribute("aria-current", "page");
    expect(await violations()).toEqual([]);
  });

  test("the collapsed rail is remembered and rendered by the server on reload", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await login(page, "member1", "/wiki");
    await page.getByRole("button", { name: "Zwiń menu" }).click();
    await expect(menu(page)).toHaveAttribute("data-sidebar", "collapsed");
    // The width animates (200 ms).
    await expect.poll(async () => (await menu(page).boundingBox())!.width).toBe(64);
    // The first HTML already has the rail: no layout jump before hydration.
    const html = await (await page.request.get("/wiki")).text();
    expect(html).toContain('data-sidebar="collapsed"');
    await page.reload();
    await expect(menu(page)).toHaveAttribute("data-sidebar", "collapsed");
    // Icons keep their names in the rail.
    await expect(page.getByRole("link", { name: "Atlas", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Rozwiń menu" }).click();
    await expect(menu(page)).toHaveAttribute("data-sidebar", "expanded");
    await expect.poll(async () => (await menu(page).boundingBox())!.width).toBe(256);
  });

  test("menu sections follow the account's roles", async ({ browser }) => {
    for (const [user, visible, hidden] of [
      ["member1", ["Moja praca", "Wiedza spółdzielni"], ["Rada Ekspertów", "Administracja"]],
      ["expert1", ["Rada Ekspertów"], ["Administracja"]],
      ["admin1", ["Administracja"], []],
    ] as const) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await login(await context.newPage(), user, "/");
      const nav = page.getByRole("navigation", { name: "Główna" });
      for (const name of visible) await expect(nav.getByRole("list", { name })).toBeVisible();
      for (const name of hidden) await expect(nav.getByRole("list", { name })).toHaveCount(0);
      await context.close();
    }
  });

  test("the note shows Źródła, Notatka and Zapytaj side by side", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await login(page, "member1", "/notebook");
    await newNote(page);
    for (const name of ["Źródła", "Notatka", "Zapytaj"]) await expect(page.getByRole("region", { name })).toBeVisible();
    await expect(page.getByRole("tablist", { name: "Widok notatki" })).toBeHidden();
    const [sources, note, ask] = await Promise.all(
      ["Źródła", "Notatka", "Zapytaj"].map(async (name) => (await page.getByRole("region", { name }).boundingBox())!.x),
    );
    expect(sources).toBeLessThan(note!);
    expect(note).toBeLessThan(ask!);
  });
});

test.describe("tablet", () => {
  test.beforeEach(() => only("tablet"));

  test("the menu is an icon rail that expands over the workspace", async ({ page }) => {
    await login(page, "member1", "/wiki");
    await expect(menu(page)).toBeVisible();
    expect((await menu(page).boundingBox())!.width).toBe(64);
    await page.getByRole("button", { name: "Rozwiń menu" }).click();
    const drawer = page.getByRole("dialog", { name: "Menu" });
    await drawer.getByRole("link", { name: "Atlas", exact: true }).click();
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("heading", { level: 1, name: "Atlas" })).toBeVisible();
  });
});

test.describe("phone", () => {
  test.beforeEach(() => only("mobile"));

  test("a top bar opens the menu as a drawer, which closes on navigation and Escape", async ({ page }) => {
    await login(page, "member1", "/notebook");
    await expect(menu(page)).toBeHidden();
    const open = page.getByRole("button", { name: "Otwórz menu" });
    await open.click();
    const drawer = page.getByRole("dialog", { name: "Menu" });
    await expect(drawer.getByRole("link", { name: "Notatnik" })).toHaveAttribute("aria-current", "page");
    await drawer.getByRole("link", { name: "Wiki" }).click();
    await expect(drawer).toBeHidden();
    await expect(page.getByRole("heading", { level: 1, name: "Baza wiedzy" })).toBeVisible();
    await open.click();
    await expect(drawer).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(open).toBeFocused();
  });

  test("the note's panels are tabs; writing, uploading and asking all work", async ({ page }) => {
    await login(page, "member1", "/notebook");
    await newNote(page);
    const tabs = page.getByRole("tablist", { name: "Widok notatki" });
    await expect(tabs.getByRole("tab", { name: "Notatka" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("region", { name: "Źródła" })).toBeHidden();

    await page.fill("#note-title", "Notatka z telefonu");
    await page.locator(".bn-editor").click();
    await page.keyboard.type("Przegląd z telefonu: rodzina 2 spokojna.");
    await expect(page.getByRole("status").filter({ hasText: "Zapisano" })).toBeVisible();

    await tabs.getByRole("tab", { name: "Źródła" }).click();
    await expect(page.getByRole("region", { name: "Notatka" })).toBeHidden();
    const upload = page.waitForResponse((r) => r.url().includes("/attachments") && r.request().method() === "POST");
    await page.getByLabel("Wybierz pliki do wgrania").setInputFiles({ name: "obserwacje.txt", mimeType: "text/plain", buffer: Buffer.from("Ramki 1-8 pełne.") });
    expect([202, 415]).toContain((await upload).status());

    await tabs.getByRole("tab", { name: "Zapytaj" }).click();
    await expect(page.getByRole("heading", { name: "Zapytaj swój notatnik" })).toBeVisible();
    await expect(page.getByLabel("Pytanie do notatnika")).toBeVisible();
    // The editor stayed mounted: the text is still there.
    await tabs.getByRole("tab", { name: "Notatka" }).click();
    await expect(page.locator(".bn-editor")).toContainText("rodzina 2 spokojna");
  });
});

test.describe("every width", () => {
  test.beforeEach(() => only("chromium", "mobile"));

  for (const [user, routes] of Object.entries(ROUTES)) {
    test(`no page scrolls sideways at 360px (${user})`, async ({ page }) => {
      await page.setViewportSize({ width: 360, height: 740 });
      await login(page, user, routes[0]!);
      for (const route of routes) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(overflow, `${route} scrolls sideways by ${overflow}px`).toBeLessThanOrEqual(0);
      }
    });

    test(`axe finds no serious or critical issue (${user})`, async ({ page }) => {
      await login(page, user, routes[0]!);
      for (const route of routes) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
        const serious = results.violations
          .filter((v) => v.impact === "serious" || v.impact === "critical")
          .map((v) => `${route}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(" ")}`);
        expect(serious).toEqual([]);
      }
    });
  }
});
