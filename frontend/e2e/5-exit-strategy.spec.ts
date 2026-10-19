/**
 * Scenario 5: Exit Strategy. A co-author revokes their contribution: the co-authored article
 * leaves the canonical index at once (the chat can no longer cite it, whatever the
 * embeddings), and it is back in the experts' queue with the revision alert.
 */

import { expect, test } from "@playwright/test";

import { apiCall, email, seed, session } from "./support/stack";

const ALERT = "Wymagana rewizja – wycofano część wkładu autorskiego";

test("a co-author's revoke degrades the article and removes it from answers", async ({ browser }) => {
  test.setTimeout(600_000);
  const title = `Odkłady ${Date.now().toString(36)}`;
  const { article_id: articleId } = seed<{ article_id: string }>({
    action: "canonical",
    title,
    blocks: [
      { email: email("member1"), text: "Odkład tworzy się z 3–4 ramek z czerwiem krytym i zapasem pokarmu." },
      { email: email("member2"), text: "Matkę do odkładu warto poddać w klateczce po dobie od jego utworzenia." },
    ],
    approver: email("expert1"),
  });

  const coauthor = await session(browser, "member2", "/me/contributions");
  const row = coauthor.locator("tr", { has: coauthor.getByRole("link", { name: title }) });
  await expect(row.getByText("współtworzony")).toBeVisible();
  await row.getByRole("button", { name: "Usuń trwale" }).click();
  const dialog = coauthor.locator("dialog[open]");
  await expect(dialog.getByText(/wrócą do Rady Ekspertów/)).toBeVisible();
  await dialog.getByLabel(/Aby potwierdzić/).fill("REVOKE");
  await dialog.getByRole("button", { name: "Usuń trwale" }).click();
  await expect(coauthor.getByRole("status").filter({ hasText: "Do recenzji wrócił 1 współtworzony artykuł" })).toBeVisible();

  // Experts: back in the queue, with the alert.
  const expert = await session(browser, "expert1", "/review");
  const item = expert.getByRole("button").filter({ hasText: title });
  await expect(item).toBeVisible();
  await expect(item).toContainText(ALERT);

  // Members: the article is no longer canonical, and no answer cites it.
  const member = await session(browser, "member1", "/chat");
  const article = await (await apiCall(member, "GET", `/wiki/articles/${articleId}`)).json();
  expect(article.status).toBe("REVISION_REQUIRED");
  expect(article.blocks.find((b: { author_id: string; content: string | null }) => b.content?.includes("klateczce"))).toBeUndefined();
  const answer = await apiCall(member, "POST", "/rag/ask", { question: `Jak tworzy się odkład? ${title}` });
  expect(answer.status()).toBe(200);
  const body = await answer.json();
  expect(body.citations.map((c: { article_id: string }) => c.article_id)).not.toContain(articleId);
});
