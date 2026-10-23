/**
 * Scenario 4: the full path of knowledge. A member's note → Push-to-Wiki (local LLM, PII
 * scrubbed) → submitted for review → approved by an expert with OTP → canonical → the
 * cooperative chat answers with a citation of that article. Uses the stack's real local
 * models, so it is slow.
 *
 * The chat question is built from the draft the model wrote, not from the note: a local
 * model may summarise details away, and that is the model's choice, not what this tests.
 */

import { expect, test } from "@playwright/test";

import { email, EMBEDDINGS_DEGENERATE, embeddingsOk, seed, session } from "./support/stack";

const TOPIC = `Zimowla ${Date.now().toString(36)}`;
const NOTE =
  "Zimowla rodzin pszczelich na pasiece leśnej. Przed zimą każda rodzina dostała 18 kg syropu " +
  "cukrowego w proporcji 3:2, karmienie zakończono do 15 września. Wylotki zwężono do 8 cm i " +
  "założono siatki przeciw myszom. Wiosną wszystkie rodziny przezimowały, osyp martwych pszczół " +
  "był niewielki. Pszczelarz Jan Kowalski, PESEL 44051401359.";

test("note → wiki draft → expert approval → cited chat answer", async ({ browser }) => {
  test.skip(!embeddingsOk(), EMBEDDINGS_DEGENERATE);
  test.setTimeout(900_000);
  // Earlier runs' articles on the same topic would compete with this one for the citation.
  seed({ action: "retire_topic", email: email("member1"), title: "%zimow%" });
  const member = await session(browser, "member1", "/notebook");

  await member.getByRole("button", { name: "Nowa notatka" }).click();
  await member.waitForURL(/\/notebook\/[0-9a-f-]{36}$/);
  await member.fill("#note-title", TOPIC);
  await member.locator(".bn-editor").click();
  await member.keyboard.type(NOTE);
  await expect(member.getByRole("status").filter({ hasText: "Zapisano" })).toBeVisible();

  await member.goto("/notebook");
  await member.getByRole("checkbox", { name: `Zaznacz: ${TOPIC}` }).check();
  await member.getByRole("button", { name: /Utwórz wniosek do Wiki/ }).click();
  await member.getByLabel("Proponowany tytuł (opcjonalnie)").fill(TOPIC);
  await member.getByRole("button", { name: "Utwórz wniosek", exact: true }).click();
  await expect(member.getByText("Szkic gotowy")).toBeVisible({ timeout: 600_000 });
  await member.getByRole("link", { name: "Zobacz szkic" }).click();
  await member.waitForURL(/\/wiki\/[0-9a-f-]{36}$/);
  const articleId = member.url().split("/").pop() ?? "";
  const draft = await member.locator("article").innerText();
  expect(draft).not.toContain("44051401359"); // PII scrubbed before the LLM saw it
  expect(draft).not.toContain("Kowalski");
  await member.getByRole("button", { name: "Wyślij do recenzji" }).click();
  await expect(member.getByText("W recenzji").first()).toBeVisible();

  const expert = await session(browser, "expert1", "/review");
  await expert.goto(`/review?article=${articleId}`);
  await expect(expert.getByRole("form", { name: "Decyzja recenzenta" })).toBeVisible();
  await expert.getByLabel("Zatwierdź (kanoniczny)").check();
  await expert.getByRole("button", { name: "Zapisz decyzję" }).click();
  await expect(expert.getByRole("status").filter({ hasText: "kanoniczny" })).toBeVisible();

  await member.goto(`/wiki/${articleId}`);
  await expect(member.getByText("Kanoniczny").first()).toBeVisible();
  await expect(member.locator("dt:has-text('Zatwierdzili') + dd")).toHaveText("Expert One");

  // A question about the approved article's first section (not a quote: the verbatim guard
  // would rightly refuse an answer that repeats the source).
  const heading = (await member.locator("article section h2, article section h3").first().textContent())?.trim() || TOPIC;
  const title = (await member.getByRole("heading", { level: 1 }).first().textContent())?.trim() || TOPIC;
  await member.goto("/chat");
  await member.getByLabel("Twoje pytanie").fill(`${title}: jakie są zalecenia spółdzielni w temacie: ${heading.toLowerCase()}?`);
  await member.getByRole("button", { name: "Zapytaj" }).click();
  const sources = member.getByRole("list", { name: "Źródła" });
  await expect(sources).toBeVisible({ timeout: 300_000 });
  await expect(sources.locator(`a[href="/wiki/${articleId}"]`)).toBeVisible();
});
