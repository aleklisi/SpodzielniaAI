/** Scenario 7: security headers on HTML and API responses; CSP blocks injected script. */

import { expect, test } from "@playwright/test";

import { login, watchCsp } from "./support/stack";

const COMMON = {
  "x-frame-options": "DENY",
  "x-content-type-options": "nosniff",
  "cross-origin-opener-policy": "same-origin",
};

test("HTML pages carry a nonce CSP with Trusted Types and the hardening headers", async ({ page }) => {
  const first = await page.goto("/");
  const second = await page.request.get("/");
  const headers = first?.headers() ?? {};
  for (const [name, value] of Object.entries(COMMON)) expect(headers[name]).toBe(value);
  expect(headers["strict-transport-security"]).toContain("max-age=");
  const csp = headers["content-security-policy"];
  expect(csp).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).toContain("require-trusted-types-for 'script'");
  expect(csp).not.toContain("unsafe-inline");
  const nonce = (h: string | undefined) => h?.match(/'nonce-([^']+)'/)?.[1];
  expect(nonce(csp)).not.toBe(nonce(second.headers()["content-security-policy"]));
  expect(headers["server"]).toBeUndefined();
});

test("API responses carry a deny-all CSP and the same headers", async ({ request }) => {
  const response = await request.get("/api/healthz");
  const headers = response.headers();
  for (const [name, value] of Object.entries(COMMON)) expect(headers[name]).toBe(value);
  expect(headers["content-security-policy"]).toContain("default-src 'none'");
  expect(headers["cache-control"]).toBe("no-store");
});

test("injected script is blocked and reported as a CSP violation", async ({ page }) => {
  const violations = await watchCsp(page);
  await login(page, "member1", "/wiki");
  await expect(page.getByRole("heading", { name: "Baza wiedzy" })).toBeVisible();
  expect(await violations()).toEqual([]); // the app itself causes none
  const outcome = await page.evaluate(() => {
    const w = window as unknown as { __pwned?: number };
    const results: string[] = [];
    try {
      const s = document.createElement("script");
      s.textContent = "window.__pwned = 1";
      document.body.append(s);
    } catch {
      results.push("inline: blocked by Trusted Types");
    }
    try {
      const s = document.createElement("script");
      s.src = "https://evil.example/x.js";
    } catch {
      results.push("src: blocked by Trusted Types");
    }
    const div = document.createElement("div");
    div.innerHTML = '<img src="x" onerror="window.__pwned = 2">'; // sanitised by the policy
    document.body.append(div);
    return { results, img: div.querySelector("img")?.getAttribute("onerror") ?? null, pwned: w.__pwned ?? 0 };
  });
  await page.waitForTimeout(300);
  expect(outcome).toEqual({
    results: ["inline: blocked by Trusted Types", "src: blocked by Trusted Types"],
    img: null,
    pwned: 0,
  });
  expect((await violations()).some((v) => v.startsWith("require-trusted-types-for"))).toBe(true);
});
