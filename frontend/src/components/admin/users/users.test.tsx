import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { UsersConsole } from "@/components/admin/users/UsersConsole";
import { ApiError } from "@/lib/api";
import { createTranslator } from "next-intl";

import { grantsElevated, lockoutReason, usersErrorKey, type UserDetail, type UserSummary } from "@/lib/users-admin";
import { MESSAGES } from "@/messages";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const NOW = new Date().toISOString();
const ADMIN_ID = "01900000-0000-7000-8000-0000000000a1";
const OLA_ID = "01900000-0000-7000-8000-0000000000b1";
const EXT_ID = "01900000-0000-7000-8000-0000000000c1";

function summary(id: string, username: string, overrides: Partial<UserSummary> = {}): UserSummary {
  return {
    id,
    username,
    display_name: username.toUpperCase(),
    email: `${username}@example.test`,
    roles: ["MEMBER"],
    status: "active",
    blocked_reason: null,
    locked_reason: null,
    required_actions: [],
    last_login_at: NOW,
    created_at: NOW,
    is_self: false,
    ...overrides,
  };
}

function detail(base: UserSummary, overrides: Partial<UserDetail> = {}): UserDetail {
  return {
    ...base,
    blocked_at: null,
    blocked_by: null,
    blocked_note: null,
    locked_at: null,
    risk_score: 0,
    created_by: null,
    other_active_admins: 1,
    idp: {
      enabled: true,
      email: base.email,
      email_verified: true,
      totp: false,
      required_actions: [],
      identity_providers: [],
    },
    ...overrides,
  };
}

const ADMIN = summary(ADMIN_ID, "admin1", { roles: ["MEMBER", "ADMIN"], is_self: true });
const OLA = summary(OLA_ID, "ola");
const EXT = summary(EXT_ID, "zewn");

const DETAILS: Record<string, UserDetail> = {
  [ADMIN_ID]: detail(ADMIN, { other_active_admins: 0, idp: { ...detail(ADMIN).idp!, totp: true } }),
  [OLA_ID]: detail(OLA),
  [EXT_ID]: detail(EXT, { idp: { ...detail(EXT).idp!, identity_providers: ["google"] } }),
};

interface Seen {
  lists: URLSearchParams[];
  bodies: Record<string, unknown>[];
}

function mount(extra: Parameters<typeof server.use> = []): Seen {
  const seen: Seen = { lists: [], bodies: [] };
  server.use(
    ...extra,
    http.get(`${API}/admin/users`, ({ request }) => {
      const params = new URL(request.url).searchParams;
      seen.lists.push(params);
      const q = params.get("q") ?? "";
      const items = [ADMIN, OLA, EXT].filter((u) => u.username?.includes(q));
      return HttpResponse.json({ items, total: items.length });
    }),
    http.get(`${API}/admin/users/:id`, ({ params }) => HttpResponse.json(DETAILS[params.id as string])),
    http.put(`${API}/admin/users/:id/roles`, async ({ request, params }) => {
      const body = (await request.json()) as Record<string, unknown>;
      seen.bodies.push({ path: `roles/${params.id as string}`, ...body });
      return HttpResponse.json({ ...DETAILS[params.id as string], roles: body.roles });
    }),
    http.post(`${API}/admin/users/:id/block`, async ({ request, params }) => {
      const body = (await request.json()) as Record<string, unknown>;
      seen.bodies.push({ path: `block/${params.id as string}`, ...body });
      return HttpResponse.json({ ...DETAILS[params.id as string], status: "blocked" });
    }),
  );
  return seen;
}

async function open(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByRole("button", { name: `Szczegóły: ${name}` }));
  return screen.findByRole("region", { name: "Szczegóły użytkownika" });
}

describe("UsersConsole", () => {
  it("lists users with their status and filters by role, status and search", async () => {
    const seen = mount();
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);

    const table = await screen.findByRole("table", { name: "Użytkownicy" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("Administrator")).toBeInTheDocument();
    expect(within(table).getAllByText("Aktywny")).toHaveLength(3);

    await user.selectOptions(screen.getByLabelText("Rola"), "EXPERT");
    await user.selectOptions(screen.getByLabelText("Status"), "blocked");
    await user.type(screen.getByLabelText("Szukaj"), "ola");
    await waitFor(() => expect(seen.lists.at(-1)?.get("q")).toBe("ola"));
    const last = seen.lists.at(-1)!;
    expect([last.get("role"), last.get("status"), last.get("offset")]).toEqual(["EXPERT", "blocked", "0"]);
  });

  it("shows a temporary password once and forgets it on close", async () => {
    mount([
      http.post(`${API}/admin/users`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        expect(body).toMatchObject({
          username: "nowa",
          email: "nowa@example.test",
          first_name: "Nowa",
          last_name: "Osoba",
          password_mode: "temporary",
          roles: ["MEMBER"],
        });
        return HttpResponse.json(
          { user: summary("01900000-0000-7000-8000-0000000000d1", "nowa", { status: "pending_setup" }), temporary_password: "Xy7-pQ3rTz9kWm2a" },
          { status: 201 },
        );
      }),
      http.get(`${API}/admin/users/01900000-0000-7000-8000-0000000000d1`, () =>
        HttpResponse.json(detail(summary("01900000-0000-7000-8000-0000000000d1", "nowa"))),
      ),
    ]);
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);

    await user.click(await screen.findByRole("button", { name: "Dodaj użytkownika" }));
    const form = screen.getByRole("form", { name: "Nowe konto" });
    await user.type(within(form).getByLabelText(/Nazwa użytkownika/), "nowa");
    await user.type(within(form).getByLabelText("E-mail"), "nowa@example.test");
    await user.type(within(form).getByLabelText("Imię"), "Nowa");
    await user.type(within(form).getByLabelText("Nazwisko"), "Osoba");
    await user.click(within(form).getByLabelText(/Hasło tymczasowe/));
    await user.click(within(form).getByRole("button", { name: "Utwórz konto" }));

    expect(await screen.findByLabelText("Hasło tymczasowe")).toHaveTextContent("Xy7-pQ3rTz9kWm2a");
    expect(screen.getByText(/tylko raz/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Gotowe" }));
    expect(screen.queryByText("Xy7-pQ3rTz9kWm2a")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dodaj użytkownika" }));
    expect(screen.queryByLabelText("Hasło tymczasowe")).not.toBeInTheDocument();
    expect(within(screen.getByRole("form", { name: "Nowe konto" })).getByLabelText(/Nazwa użytkownika/)).toHaveValue("");
  });

  it("asks before granting a privileged role and names it", async () => {
    const seen = mount();
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);
    const panel = await open(user, "OLA");

    await user.click(within(panel).getByLabelText(/^Ekspert/));
    await user.click(within(panel).getByRole("button", { name: "Zapisz role" }));
    const confirm = await screen.findByRole("dialog", { name: "Nadać uprawnienia?" });
    expect(confirm).toHaveTextContent("Nadajesz rolę: Ekspert");
    expect(confirm).toHaveTextContent(/skonfiguruje aplikację do kodów/);
    await user.click(within(confirm).getByRole("button", { name: "Nadaj" }));

    await waitFor(() => expect(seen.bodies).toEqual([{ path: `roles/${OLA_ID}`, roles: ["MEMBER", "EXPERT"] }]));
  });

  it("explains why the own account and the last admin are protected", async () => {
    mount();
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);
    const panel = await open(user, "ADMIN1");

    expect(within(panel).getByText("To Ty")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Zablokuj" })).toBeDisabled();
    expect(within(panel).getByText("Nie można zablokować: To Twoje konto.")).toBeInTheDocument();
    expect(within(panel).getByLabelText(/^Administrator/)).toBeDisabled();
    expect(within(panel).getByLabelText(/^Członek/)).toBeDisabled();
  });

  it("sends the admin to log in again when the API wants a fresh second factor", async () => {
    mount([
      http.put(`${API}/admin/users/:id/roles`, () =>
        HttpResponse.json(
          { detail: { error: "reauthentication_required", max_age_seconds: 300, step_up: "/api/auth/login?prompt=login&acr_values=mfa" } },
          { status: 403 },
        ),
      ),
    ]);
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);
    const panel = await open(user, "OLA");

    await user.click(within(panel).getByLabelText(/^Administrator/));
    await user.click(within(panel).getByRole("button", { name: "Zapisz role" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Nadaj" }));

    const link = await within(panel).findByRole("link", { name: "Zaloguj ponownie z MFA" });
    expect(link.getAttribute("href")).toContain("prompt=login&acr_values=mfa&return_to=");
    // The checkbox goes back to the saved state.
    expect(within(panel).getByLabelText(/^Administrator/)).not.toBeChecked();
  });

  it("hides password actions for users of an external identity provider", async () => {
    mount();
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);
    const panel = await open(user, "ZEWN");

    expect(within(panel).getByText("google")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Resetuj hasło" })).not.toBeInTheDocument();
    expect(within(panel).getByText(/przez zewnętrznego dostawcę/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Zablokuj" })).toBeEnabled();
  });

  it("blocks with a reason and a note", async () => {
    const seen = mount();
    const user = userEvent.setup();
    renderWithClient(<UsersConsole />);
    const panel = await open(user, "OLA");

    await user.click(within(panel).getByRole("button", { name: "Zablokuj" }));
    const dialog = await screen.findByRole("dialog", { name: "Zablokować konto?" });
    await user.selectOptions(within(dialog).getByLabelText("Powód"), "security");
    await user.type(within(dialog).getByLabelText(/Notatka/), "Podejrzane logowania");
    await user.click(within(dialog).getByRole("button", { name: "Zablokuj konto" }));

    await waitFor(() =>
      expect(seen.bodies).toEqual([{ path: `block/${OLA_ID}`, reason: "security", note: "Podejrzane logowania" }]),
    );
    expect(await screen.findByText("Konto zablokowane, sesje zakończone.")).toBeInTheDocument();
  });
});

describe("users-admin helpers", () => {
  it("maps API refusals to Polish explanations", () => {
    const refusal = (error: string, status = 409) => new ApiError(status, { detail: { error } });
    const t = createTranslator({ locale: "pl", messages: MESSAGES.pl, namespace: "adminUsers.errors" });
    const usersError = (error: unknown) => t(usersErrorKey(error));
    expect(usersError(refusal("last_admin"))).toMatch(/ostatni aktywny administrator/);
    expect(usersError(refusal("email_failed", 502))).toMatch(/hasło tymczasowe/);
    expect(usersError(new ApiError(429, null))).toMatch(/Odczekaj/);
    expect(usersError(new ApiError(422, { detail: [] }))).toBe("Sprawdź wprowadzone wartości.");
  });

  it("knows which grants need a confirmation and when an admin is the last one", () => {
    expect(grantsElevated(["MEMBER"], ["MEMBER", "EXPERT", "ADMIN"])).toEqual(["EXPERT", "ADMIN"]);
    expect(grantsElevated(["MEMBER", "EXPERT"], ["MEMBER"])).toEqual([]);
    const lastAdmin = { ...DETAILS[ADMIN_ID], is_self: false };
    expect(lockoutReason(lastAdmin)).toBe("lastAdmin");
    expect(lockoutReason({ ...lastAdmin, other_active_admins: 2 })).toBeNull();
    expect(lockoutReason(DETAILS[OLA_ID])).toBeNull();
  });
});

describe("in English (14.4)", () => {
  it("lists users with English roles and statuses", async () => {
    setTestLocale("en");
    mount();
    renderWithClient(<UsersConsole />);
    const table = await screen.findByRole("table", { name: "Users" });
    expect(within(table).getByText("Administrator")).toBeInTheDocument();
    expect(within(table).getAllByText("Active")).toHaveLength(3);
    expect(screen.getByLabelText("Role")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add user" })).toBeInTheDocument();
  });
});
