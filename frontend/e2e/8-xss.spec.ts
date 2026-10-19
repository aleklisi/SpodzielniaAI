/** Scenario 8: a Wiki article with hostile Markdown renders inert. */

import { expect, test } from "@playwright/test";

import { email, login, seed, watchCsp } from "./support/stack";

const HOSTILE = [
  "Leczenie zimowe **kwasem szczawiowym**.",
  '<img src=x onerror="window.__pwned=1">',
  "<script>window.__pwned=2</script>",
  "[kliknij](javascript:window.__pwned=3)",
  "[dane](data:text/html;base64,PHNjcmlwdD53aW5kb3cuX19wd25lZD00PC9zY3JpcHQ+)",
  '<iframe src="https://evil.example"></iframe>',
  "<svg><script>window.__pwned=5</script></svg>",
  "![piksel](https://evil.example/track.gif)",
].join("\n\n");

test("hostile Markdown in a canonical article stays inert", async ({ page }) => {
  const { article_id } = seed<{ article_id: string }>({
    action: "canonical",
    title: `E2E XSS ${Date.now()}`,
    blocks: [{ email: email("member2"), text: HOSTILE }],
    approver: email("expert1"),
  });
  const violations = await watchCsp(page);
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await login(page, "member1", `/wiki/${article_id}`);
  const article = page.locator("article");
  await expect(article.getByText("kwasem szczawiowym")).toBeVisible();
  expect(await article.locator("script, img, iframe, svg, object, embed").count()).toBe(0);
  for (const href of await article.locator("a").evaluateAll((as) => as.map((a) => a.getAttribute("href") ?? ""))) {
    expect(href).toMatch(/^(https:|\/|#)/);
  }
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned ?? 0)).toBe(0);
  expect(dialogs).toEqual([]);
  expect(await violations()).toEqual([]);
});
