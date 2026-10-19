/** Scenario 2: private notebooks are isolated (Postgres RLS; 404, never 403, for others). */

import { expect, test } from "@playwright/test";

import { apiCall, session } from "./support/stack";

test("another member can't open a note, in the UI or the API", async ({ browser }) => {
  const owner = await session(browser, "member1", "/notebook");
  await owner.getByRole("button", { name: "Nowa notatka" }).click();
  await owner.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
  const url = owner.url();
  const noteId = url.split("/").pop() ?? "";
  await owner.locator(".bn-editor").click();
  await owner.keyboard.type("Prywatna obserwacja: pasieka przy lesie, 12 uli.");
  await expect(owner.getByRole("status").filter({ hasText: "Zapisano" })).toBeVisible();

  const other = await session(browser, "member2", "/notebook");
  await other.goto(url);
  await expect(other.getByRole("heading", { name: "Nie znaleziono notatki" })).toBeVisible();
  const direct = await apiCall(other, "GET", `/notes/${noteId}`);
  expect(direct.status()).toBe(404);
  expect(await direct.text()).not.toContain("pasieka");
  // The owner still sees it.
  expect((await apiCall(owner, "GET", `/notes/${noteId}`)).status()).toBe(200);
});
