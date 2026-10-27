import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeDashboard } from "@/components/home/HomeDashboard";
import { AppShell } from "@/components/shell/AppShell";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const nav = vi.hoisted(() => ({ path: "/wiki", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path, useRouter: () => ({ push: nav.push }) }));
const reload = vi.hoisted(() => vi.fn());
vi.mock("@/i18n/locales", async (original) => ({ ...(await original<typeof import("@/i18n/locales")>()), reloadPage: reload }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  reload.mockReset();
  document.cookie = "lang=; Max-Age=0; Path=/";
  sessionStorage.clear();
});

function me(locale: "pl" | "en" = "pl", chosen = false) {
  server.use(
    http.get(`${API}/me`, () =>
      HttpResponse.json({ id: "u1", display_name: "Anna", email: null, roles: ["MEMBER"], mfa_satisfied: true, locale, locale_chosen: chosen }),
    ),
  );
}

const menu = () => within(screen.getAllByRole("complementary")[0]!);

function renderShell() {
  return renderWithClient(
    <AppShell initialCollapsed={false}>
      <HomeDashboard />
    </AppShell>,
  );
}

describe("LanguageSwitch", () => {
  it("names both languages in their own language", async () => {
    me();
    renderShell();
    const group = await menu().findByRole("group", { name: "Język" });
    const polish = within(group).getByRole("radio", { name: "Polski" });
    const english = within(group).getByRole("radio", { name: "English" });
    expect(polish).toBeChecked();
    expect(polish.closest("label")).toHaveAttribute("lang", "pl");
    expect(english.closest("label")).toHaveAttribute("lang", "en");
  });

  it("saves the choice on the account, sets the cookie and reloads", async () => {
    me();
    const saved: unknown[] = [];
    server.use(
      http.put(`${API}/me/locale`, async ({ request }) => {
        saved.push(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderShell();
    await screen.findByText("Dzień dobry, Anna");
    await userEvent.click(within(await menu().findByRole("group", { name: "Język" })).getByRole("radio", { name: "English" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(saved).toEqual([{ locale: "en" }]);
    expect(document.cookie).toContain("lang=en");
  });

  it("keeps the page when saving fails, and says so", async () => {
    me();
    server.use(http.put(`${API}/me/locale`, () => HttpResponse.json({ detail: { error: "rate_limited" } }, { status: 429 })));
    renderShell();
    await screen.findByText("Dzień dobry, Anna");
    await userEvent.click(within(await menu().findByRole("group", { name: "Język" })).getByRole("radio", { name: "English" }));
    expect(await menu().findByRole("alert")).toHaveTextContent("Nie udało się zmienić języka");
    expect(reload).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain("lang=en");
  });

  it("lets a signed-out visitor switch with the cookie alone", async () => {
    server.use(http.get(`${API}/me`, () => HttpResponse.json({ detail: "unauthorized" }, { status: 401 })));
    renderShell();
    await menu().findByRole("link", { name: "Zaloguj się" });
    await userEvent.click(within(menu().getByRole("group", { name: "Język" })).getByRole("radio", { name: "English" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(document.cookie).toContain("lang=en");
  });

  it("follows a choice made on another device, once", async () => {
    me("en", true);
    renderShell();
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(document.cookie).toContain("lang=en");
    expect(sessionStorage.getItem("lang-synced")).toBe("en");
  });

  it("never reloads in a loop when the cookie does not stick", async () => {
    me("en", true);
    sessionStorage.setItem("lang-synced", "en");
    renderShell();
    await screen.findByText("Dzień dobry, Anna");
    expect(reload).not.toHaveBeenCalled();
  });
});

describe("in English", () => {
  it("renders the menu, the home page and the switch in English", async () => {
    setTestLocale("en");
    me("en", true);
    renderShell();
    expect(await screen.findByText("Hello, Anna")).toBeInTheDocument();
    expect(menu().getByRole("list", { name: "My work" })).toBeInTheDocument();
    expect(menu().getByRole("link", { name: "Notebook" })).toBeInTheDocument();
    expect(menu().getByRole("link", { name: "Your data" })).toBeInTheDocument();
    expect(within(menu().getByRole("group", { name: "Language" })).getByRole("radio", { name: "English" })).toBeChecked();
    expect(screen.getByRole("main")).toHaveTextContent("Articles approved by the Expert Council.");
    expect(reload).not.toHaveBeenCalled();
  });
});

describe("before the account is known", () => {
  it("waits for /me before saving anywhere", async () => {
    let answer: (() => void) | undefined;
    server.use(
      http.get(`${API}/me`, async () => {
        await new Promise<void>((resolve) => (answer = resolve));
        return HttpResponse.json({ id: "u1", display_name: "Anna", email: null, roles: ["MEMBER"], mfa_satisfied: true, locale: "pl", locale_chosen: false });
      }),
    );
    renderShell();
    const english = within(menu().getByRole("group", { name: "Język" })).getByRole("radio", { name: "English" });
    expect(english).toBeDisabled();
    await waitFor(() => expect(answer).toBeDefined());
    answer?.();
    await waitFor(() => expect(english).toBeEnabled());
  });
});
