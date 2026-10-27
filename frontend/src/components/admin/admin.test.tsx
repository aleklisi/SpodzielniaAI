import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { CoverageHistogram } from "@/components/admin/CoverageHistogram";
import { riskPoints } from "@/components/admin/RiskHistory";
import { SecurityDashboard } from "@/components/admin/SecurityDashboard";
import { AuditLog } from "@/components/admin/Tables";
import { histogram, type UserCoverage } from "@/lib/admin";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function user(id: string, overrides: Partial<UserCoverage> = {}): UserCoverage {
  return {
    user_id: id, display_name: `Konto ${id}`, email: null, roles: ["MEMBER"], is_locked: false, locked_reason: null,
    locked_at: null, ratios: { "24h": 0.01, "7d": 0.02, "30d": 0.02 }, blocks: { "24h": 2, "7d": 4, "30d": 4 },
    fraction_of_limit: 0.2, pressure: 0.1, incidents: 0, risk: 0.05, level: "NORMAL", ...overrides,
  };
}

const entry = (id: number, event_type: string, payload: Record<string, unknown> = {}) => ({
  id, occurred_at: "2026-10-19T10:00:00Z", actor_id: "0190f5a8-0000-7000-8000-000000000001", event_type,
  target_type: null, target_id: null, payload,
});

describe("coverage histogram", () => {
  it("buckets accounts by their closest window", () => {
    expect(histogram([0.1, 0.24, 0.25, 0.6, 0.99, 1, 3].map((f) => ({ fraction_of_limit: f })))).toEqual([2, 1, 1, 1, 2]);
  });

  it("offers every value in a table and on focus", async () => {
    renderWithClient(<CoverageHistogram users={[user("a"), user("b", { fraction_of_limit: 1.4 })]} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row").map((r) => r.textContent)).toContain("≥ 100%1");
    screen.getByRole("button", { name: "≥ 100% limitu: 1 konto" }).focus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("1 konto");
  });
});

it("reads risk points from shield decisions, oldest first", () => {
  const points = riskPoints([
    { ...entry(2, "SHIELD_DECISION", { score: 0.62, level: "HIGH" }), occurred_at: "2026-10-19T11:00:00Z" },
    { ...entry(1, "SHIELD_DECISION", { score: 0.35, level: "ELEVATED" }), occurred_at: "2026-10-19T10:00:00Z" },
    { ...entry(3, "SHIELD_DECISION", {}), occurred_at: "2026-10-19T12:00:00Z" },
  ]);
  expect(points.map((p) => [p.score, p.level])).toEqual([[0.35, "ELEVATED"], [0.62, "HIGH"]]);
});

describe("audit log", () => {
  it("filters by event and pages with the cursor", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/admin/audit-log`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        seen.push(params);
        return params.get("cursor")
          ? HttpResponse.json({ items: [entry(1, "COVERAGE_LOCK")], next_cursor: null })
          : HttpResponse.json({ items: [entry(3, "HONEYPOT_TRIGGERED"), entry(2, "HONEYPOT_TRIGGERED")], next_cursor: 2 });
      }),
    );
    renderWithClient(<AuditLog />);
    await userEvent.selectOptions(await screen.findByLabelText("Zdarzenie"), "HONEYPOT_TRIGGERED");
    await waitFor(() => expect(seen.at(-1)?.get("event_type")).toBe("HONEYPOT_TRIGGERED"));
    expect(seen.at(-1)?.get("since")).not.toBeNull(); // 7 days by default
    await userEvent.click(await screen.findByRole("button", { name: "Starsze" }));
    await waitFor(() => expect(seen.at(-1)?.get("cursor")).toBe("2"));
    expect(await screen.findByRole("cell", { name: "Przekroczony budżet pokrycia" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Wszystko" }));
    await waitFor(() => expect(seen.at(-1)?.get("since")).toBeNull());
    expect(seen.at(-1)?.get("cursor")).toBeNull(); // a new filter starts from the top
  });

  it("renders payload values as text", async () => {
    server.use(
      http.get(`${API}/admin/audit-log`, () =>
        HttpResponse.json({ items: [entry(1, "VERBATIM_BLOCKED", { reason: '<img src=x onerror="alert(1)">' })], next_cursor: null }),
      ),
    );
    const { container } = renderWithClient(<AuditLog />);
    expect(await screen.findByText(/reason=<img/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("SecurityDashboard", () => {
  function stack(overrides: { unlock?: (body: unknown) => void } = {}) {
    server.use(
      http.get(`${API}/admin/coverage`, () =>
        HttpResponse.json({
          corpus_blocks: 200, limits: { "24h": 0.05, "7d": 0.15, "30d": 0.25 }, min_blocks_before_lock: 50,
          users: [user("locked1", { is_locked: true, locked_reason: "honeypot", level: "CRITICAL", risk: 0.9 }), user("ok1")],
        }),
      ),
      http.get(`${API}/admin/alerts`, () =>
        HttpResponse.json([entry(5, "HONEYPOT_TRIGGERED", { triggers: 1 }), entry(4, "HONEYPOT_TRIGGERED"), entry(3, "VERBATIM_BLOCKED")]),
      ),
      http.get(`${API}/admin/audit-log/verify`, () => HttpResponse.json({ ok: true, rows: 123, head_id: 123, head_hash: "ab".repeat(32), breaks: [] })),
      http.get(`${API}/admin/audit-log`, () => HttpResponse.json({ items: [], next_cursor: null })),
      http.post(`${API}/admin/users/:id/unlock`, async ({ request, params }) => {
        overrides.unlock?.({ id: params.id, ...((await request.json()) as object) });
        return HttpResponse.json({ user_id: params.id, is_locked: false });
      }),
    );
  }

  it("shows the KPIs and unlocks an account with a reason", async () => {
    let body: unknown = null;
    stack({ unlock: (b) => (body = b) });
    renderWithClient(<SecurityDashboard />);
    const kpis = await screen.findByLabelText("Wskaźniki");
    await waitFor(() => expect(kpis).toHaveTextContent("Zablokowane konta1"));
    expect(kpis).toHaveTextContent("Pobrane kanarki2");
    expect(kpis).toHaveTextContent("Zablokowane odpowiedzi dosłowne1");
    expect(kpis).toHaveTextContent("Nienaruszony");
    expect(screen.getByText("Krytyczny")).toBeInTheDocument(); // level in words, not only color

    await userEvent.click(screen.getByRole("button", { name: "Odblokuj" }));
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Odblokuj" });
    expect(submit).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Uzasadnienie/), "Wyjaśnione z członkiem.");
    await userEvent.click(submit);
    await waitFor(() => expect(body).toEqual({ id: "locked1", reason: "Wyjaśnione z członkiem.", reset_risk: true }));
  });

  it("asks for MFA when the API requires it", async () => {
    const stepUp = HttpResponse.json({ detail: { error: "mfa_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 });
    server.use(
      http.get(`${API}/admin/coverage`, () => stepUp.clone()),
      http.get(`${API}/admin/alerts`, () => stepUp.clone()),
      http.get(`${API}/admin/audit-log/verify`, () => stepUp.clone()),
      http.get(`${API}/admin/audit-log`, () => stepUp.clone()),
    );
    renderWithClient(<SecurityDashboard />);
    expect(await screen.findByRole("link", { name: "Zaloguj ponownie z MFA" })).toBeInTheDocument();
  });
});

it("declines accounts and blocks the Polish way", async () => {
  const { createTranslator } = await import("next-intl");
  const { MESSAGES } = await import("@/messages");
  const t = createTranslator({ locale: "pl", messages: MESSAGES.pl, namespace: "adminSecurity" });
  expect([1, 3, 5, 22].map((count) => t("accounts", { count }))).toEqual(["1 konto", "3 konta", "5 kont", "22 konta"]);
  expect(t("blocks", { count: 1 })).toBe("1 blok");
});
