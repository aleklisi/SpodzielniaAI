import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ContributionsPanel } from "@/components/contributions/ContributionsPanel";
import { RevokeDialog } from "@/components/contributions/RevokeDialog";
import { createTranslator } from "next-intl";

import { describeResult } from "@/lib/contributions";
import { MESSAGES } from "@/messages";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ARTICLE = "0190f5a8-0000-7000-8000-0000000000a1";
const result = { action: "REVOKE" as const, blocks_affected: 2, articles: [ARTICLE], degraded: [ARTICLE], withdrawn: [], index_synced: true };

describe("RevokeDialog", () => {
  it("stays disabled until REVOKE is typed and waits for the server", async () => {
    let body: unknown = null;
    let release: () => void = () => {};
    server.use(
      http.post(`${API}/me/contributions/revoke`, async ({ request }) => {
        body = await request.json();
        await new Promise<void>((resolve) => (release = resolve));
        return HttpResponse.json(result);
      }),
    );
    const done: unknown[] = [];
    renderWithClient(
      <RevokeDialog open onClose={() => {}} articleIds={[ARTICLE]} scopeLabel="2 bloki" coAuthored onDone={(r) => done.push(r)} />,
    );
    const confirm = screen.getByRole("button", { name: "Usuń trwale" });
    expect(screen.getByText(/wrócą do Rady Ekspertów/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Aby potwierdzić/), "revoke");
    expect(confirm).toBeDisabled(); // exact word, case sensitive
    await userEvent.clear(screen.getByLabelText(/Aby potwierdzić/));
    await userEvent.type(screen.getByLabelText(/Aby potwierdzić/), "REVOKE");
    await userEvent.click(confirm);

    await waitFor(() => expect(body).toEqual({ article_ids: [ARTICLE], confirm: "REVOKE" }));
    expect(done).toEqual([]); // no optimistic success
    expect(confirm).toHaveAttribute("aria-busy", "true");
    release();
    await waitFor(() => expect(done).toEqual([result]));
  });

  it("asks for a fresh login when the server wants one", async () => {
    server.use(
      http.post(`${API}/me/contributions/revoke`, () =>
        HttpResponse.json(
          { detail: { error: "reauthentication_required", step_up: "/api/auth/login?prompt=login" } },
          { status: 403 },
        ),
      ),
    );
    renderWithClient(<RevokeDialog open onClose={() => {}} articleIds={null} scopeLabel="wszystko" coAuthored={false} onDone={() => {}} />);
    await userEvent.type(screen.getByLabelText(/Aby potwierdzić/), "REVOKE");
    await userEvent.click(screen.getByRole("button", { name: "Usuń trwale" }));
    const link = await screen.findByRole("link", { name: "Zaloguj ponownie z MFA" });
    expect(link.getAttribute("href")).toMatch(/^\/api\/auth\/login\?prompt=login&return_to=/);
  });
});

describe("ContributionsPanel", () => {
  it("lists contributions and suspends one article", async () => {
    let suspended: unknown = null;
    server.use(
      http.get(`${API}/me/contributions`, () =>
        HttpResponse.json([
          {
            article_id: ARTICLE,
            title: "Leczenie warrozy",
            status: "CANONICAL",
            co_authored: true,
            blocks: [
              { id: "b1", position: 0, consent_status: "ACTIVE" },
              { id: "b2", position: 1, consent_status: "SUSPENDED" },
            ],
          },
        ]),
      ),
      http.get(`${API}/me/contributions/history`, () => HttpResponse.json([])),
      http.post(`${API}/me/contributions/suspend`, async ({ request }) => {
        suspended = await request.json();
        return HttpResponse.json({ ...result, action: "SUSPEND", blocks_affected: 1 });
      }),
    );
    renderWithClient(<ContributionsPanel />);
    const row = (await screen.findByRole("link", { name: "Leczenie warrozy" })).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText("1 aktywne")).toBeInTheDocument();
    expect(cells.getByText("1 wstrzymane")).toBeInTheDocument();
    expect(cells.getByText("współtworzony")).toBeInTheDocument();
    await userEvent.click(cells.getByRole("button", { name: "Wstrzymaj" }));
    await waitFor(() => expect(suspended).toEqual({ article_ids: [ARTICLE] }));
    expect(await screen.findByText("Wstrzymano 1 blok. Do recenzji wrócił 1 współtworzony artykuł.")).toBeInTheDocument();
  });
});

const translator = (locale: "pl" | "en") =>
  createTranslator({ locale: locale === "en" ? "en-GB" : "pl", messages: MESSAGES[locale], namespace: "contributions" });

it("declines the Polish way", () => {
  const t = translator("pl");
  expect(describeResult(t, "revoke", { ...result, blocks_affected: 3, degraded: ["a", "b"], withdrawn: ["c", "d", "e", "f", "g"] })).toBe(
    "Usunięto trwale 3 bloki. Do recenzji wróciły 2 współtworzone artykuły. Wycofano z bazy wiedzy 5 artykułów.",
  );
  expect([1, 2, 4, 5, 12, 22, 25, 112].map((count) => t("history.blocks", { count }))).toEqual(
    [" · 1 blok", " · 2 bloki", " · 4 bloki", " · 5 bloków", " · 12 bloków", " · 22 bloki", " · 25 bloków", " · 112 bloków"],
  );
});

it("says it in English", () => {
  const t = translator("en");
  expect(describeResult(t, "suspend", { ...result, blocks_affected: 1, degraded: [ARTICLE], withdrawn: [], index_synced: false })).toBe(
    "Suspended 1 block. 1 co-authored article went back to review. Search will update in a moment.",
  );
});
