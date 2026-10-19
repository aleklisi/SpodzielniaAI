import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { RequireRole } from "@/components/auth/RequireRole";
import { DecisionForm } from "@/components/review/DecisionForm";
import { orderQueue } from "@/components/review/ReviewPanel";
import { validateDecision } from "@/components/review/validate";
import type { Article, QueueItem } from "@/lib/wiki";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ID = "0190f5a8-0000-7000-8000-0000000000a1";
const me = (roles: string[]) =>
  http.get(`${API}/me`, () => HttpResponse.json({ id: "u1", roles, mfa_satisfied: true, email: null, display_name: "Ewa" }));

const article: Article = {
  id: ID,
  title: "Leczenie warrozy",
  status: "IN_REVIEW",
  version: 3,
  last_verified_at: null,
  authors: [{ id: "u2", display_name: "Anna" }],
  approving_experts: [],
  supersedes_id: null,
  superseded_by_id: null,
  review_alert: null,
  blocks: [],
};

describe("role guards", () => {
  it("keeps members out of the review panel", async () => {
    server.use(me(["MEMBER"]));
    renderWithClient(<RequireRole role="EXPERT"><p>kolejka</p></RequireRole>);
    expect(await screen.findByRole("heading", { name: "Brak dostępu" })).toBeInTheDocument();
    expect(screen.queryByText("kolejka")).toBeNull();
  });

  it("lets experts in", async () => {
    server.use(me(["MEMBER", "EXPERT"]));
    renderWithClient(<RequireRole role="EXPERT"><p>kolejka</p></RequireRole>);
    expect(await screen.findByText("kolejka")).toBeInTheDocument();
  });
});

describe("decision validation", () => {
  it.each([
    [{ decision: null, comment: "", supersedesId: null }, "Wybierz decyzję."],
    [{ decision: "REJECT", comment: "nie", supersedesId: null }, "Uzasadnij decyzję"],
    [{ decision: "REQUEST_CHANGES", comment: "", supersedesId: null }, "Uzasadnij decyzję"],
    [{ decision: "REJECT", comment: "Brak źródeł i sprzeczne dawki.", supersedesId: "x" }, "tylko przy zatwierdzeniu"],
    [{ decision: "APPROVE", comment: "", supersedesId: ID }, "samego siebie"],
  ] as const)("refuses %j", (draft, message) => {
    expect(validateDecision(draft, ID).join(" ")).toContain(message);
  });

  it("accepts a plain approval and a reasoned rejection", () => {
    expect(validateDecision({ decision: "APPROVE", comment: "", supersedesId: null }, ID)).toEqual([]);
    expect(validateDecision({ decision: "REJECT", comment: "Brak źródeł i sprzeczne dawki.", supersedesId: null }, ID)).toEqual([]);
  });
});

it("puts articles sent back by the system first", () => {
  const item = (id: string, alert: string | null, at: string): QueueItem => ({
    article_id: id, title: id, status: "IN_REVIEW", version: 1, review_alert: alert,
    status_changed_at: at, approvals: 0, quorum: 1, is_own: false,
  });
  const ordered = orderQueue([item("a", null, "2026-10-01"), item("b", "Wymagana rewizja", "2026-10-05"), item("c", null, "2026-09-01")]);
  expect(ordered.map((i) => i.article_id)).toEqual(["b", "c", "a"]);
});

describe("DecisionForm", () => {
  it("explains why an author can't review their own article", () => {
    renderWithClient(<DecisionForm article={article} isOwn />);
    expect(screen.getByRole("note")).toHaveTextContent("Jesteś współautorem");
    expect(screen.queryByRole("button", { name: "Zapisz decyzję" })).toBeNull();
  });

  it("blocks an unexplained rejection before sending", async () => {
    let sent = false;
    server.use(
      http.get(`${API}/wiki/articles`, () => HttpResponse.json([])),
      http.post(`${API}/reviews/:id`, () => {
        sent = true;
        return HttpResponse.json({});
      }),
    );
    renderWithClient(<DecisionForm article={article} isOwn={false} />);
    await userEvent.click(screen.getByLabelText("Odrzuć"));
    await userEvent.click(screen.getByRole("button", { name: "Zapisz decyzję" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Uzasadnij decyzję");
    expect(sent).toBe(false);
  });

  it("sends the version it was made on and reports a conflict", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.get(`${API}/wiki/articles`, () => HttpResponse.json([])),
      http.post(`${API}/reviews/:id`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ detail: { error: "version_conflict" } }, { status: 409 });
      }),
    );
    renderWithClient(<DecisionForm article={article} isOwn={false} />);
    await userEvent.click(screen.getByLabelText("Zatwierdź (kanoniczny)"));
    await userEvent.click(screen.getByRole("button", { name: "Zapisz decyzję" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Artykuł zmienił się w międzyczasie");
    await waitFor(() => expect(body).toMatchObject({ decision: "APPROVE", version: 3, supersedes_id: null }));
  });

  it("asks for MFA step-up when the API requires it", async () => {
    server.use(
      http.get(`${API}/wiki/articles`, () => HttpResponse.json([])),
      http.post(`${API}/reviews/:id`, () =>
        HttpResponse.json({ detail: { error: "mfa_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 }),
      ),
    );
    renderWithClient(<DecisionForm article={article} isOwn={false} />);
    await userEvent.click(screen.getByLabelText("Zatwierdź (kanoniczny)"));
    await userEvent.click(screen.getByRole("button", { name: "Zapisz decyzję" }));
    const link = await screen.findByRole("link", { name: "Zaloguj ponownie z MFA" });
    expect(link.getAttribute("href")).toContain("/api/auth/login?prompt=login&return_to=");
  });
});
