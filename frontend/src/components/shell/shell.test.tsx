import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeDashboard } from "@/components/home/HomeDashboard";
import { AppShell } from "@/components/shell/AppShell";
import { activeHref } from "@/components/shell/nav-items";
import { API, renderWithClient } from "@/test/render";

const nav = vi.hoisted(() => ({ path: "/notebook", push: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.path, useRouter: () => ({ push: nav.push }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  nav.path = "/notebook";
  nav.push.mockReset();
  document.cookie = "sb=; Max-Age=0; Path=/";
});

function me(roles: string[]) {
  server.use(http.get(`${API}/me`, () => HttpResponse.json({ id: "u1", display_name: "Anna", email: null, roles, mfa_satisfied: true })));
}

function renderShell(collapsed = false) {
  return renderWithClient(
    <AppShell initialCollapsed={collapsed}>
      <p>treść</p>
    </AppShell>,
  );
}

const desktop = () => screen.getAllByRole("complementary")[0]!;

describe("activeHref", () => {
  it("picks the longest matching parent", () => {
    const hrefs = ["/review", "/review/atlas", "/atlas", "/admin/atlas"];
    expect(activeHref("/review/atlas", hrefs)).toBe("/review/atlas");
    expect(activeHref("/review", hrefs)).toBe("/review");
    expect(activeHref("/admin/atlas", hrefs)).toBe("/admin/atlas");
    expect(activeHref("/atlas/123", hrefs)).toBe("/atlas");
    expect(activeHref("/atlasy", hrefs)).toBeUndefined();
  });
});

describe("AppShell", () => {
  it("shows the member's sections and marks the current page", async () => {
    me(["MEMBER"]);
    nav.path = "/notebook/n1";
    renderShell();
    const menu = within(desktop());
    expect(await menu.findByRole("list", { name: "Moja praca" })).toBeInTheDocument();
    expect(menu.getByRole("list", { name: "Wiedza spółdzielni" })).toBeInTheDocument();
    expect(menu.queryByRole("list", { name: "Rada Ekspertów" })).not.toBeInTheDocument();
    expect(menu.queryByRole("list", { name: "Administracja" })).not.toBeInTheDocument();
    expect(menu.getByRole("link", { name: "Notatnik" })).toHaveAttribute("aria-current", "page");
    expect(menu.getByRole("link", { name: "Wiki" })).not.toHaveAttribute("aria-current");
    expect(menu.getByRole("link", { name: "Twoje dane" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("main")).toHaveTextContent("treść");
  });

  it("shows expert and admin sections to those roles", async () => {
    me(["MEMBER", "EXPERT", "ADMIN"]);
    nav.path = "/review/atlas";
    renderShell();
    const menu = within(desktop());
    expect(await menu.findByRole("list", { name: "Administracja" })).toBeInTheDocument();
    expect(menu.getByRole("link", { name: "Recenzje atlasu" })).toHaveAttribute("aria-current", "page");
    expect(menu.getByRole("link", { name: "Recenzje" })).not.toHaveAttribute("aria-current");
  });

  it("offers only signing in to a visitor", async () => {
    server.use(http.get(`${API}/me`, () => HttpResponse.json({ detail: "unauthorized" }, { status: 401 })));
    renderShell();
    const menu = within(desktop());
    expect(await menu.findByRole("link", { name: "Zaloguj się" })).toBeInTheDocument();
    expect(menu.queryByRole("list")).not.toBeInTheDocument();
    expect(menu.queryByRole("button", { name: "Utwórz notatkę" })).not.toBeInTheDocument();
  });

  it("collapses to the rail and remembers it in the cookie", async () => {
    me(["MEMBER"]);
    renderShell();
    expect(desktop()).toHaveAttribute("data-sidebar", "expanded");
    await userEvent.click(within(desktop()).getByRole("button", { name: "Zwiń menu" }));
    expect(desktop()).toHaveAttribute("data-sidebar", "collapsed");
    expect(document.cookie).toContain("sb=1");
    // The first is the lg+ toggle; the second (md–lg, hidden by CSS on lg) opens the drawer.
    await userEvent.click(within(desktop()).getAllByRole("button", { name: "Rozwiń menu" })[0]!);
    expect(desktop()).toHaveAttribute("data-sidebar", "expanded");
    expect(document.cookie).toContain("sb=0");
  });

  it("starts collapsed from the cookie value", () => {
    me(["MEMBER"]);
    renderShell(true);
    expect(desktop()).toHaveAttribute("data-sidebar", "collapsed");
  });

  it("opens the phone drawer and closes it after navigating", async () => {
    me(["MEMBER"]);
    renderShell();
    await userEvent.click(screen.getByRole("button", { name: "Otwórz menu" }));
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    expect(drawer).toHaveAttribute("open");
    await userEvent.click(await within(drawer).findByRole("link", { name: "Wiki" }));
    expect(drawer).not.toHaveAttribute("open");
    await userEvent.click(screen.getByRole("button", { name: "Otwórz menu" }));
    await userEvent.click(within(drawer).getByRole("button", { name: "Zamknij menu" }));
    expect(drawer).not.toHaveAttribute("open");
  });

  it("creates a note from the menu", async () => {
    me(["MEMBER"]);
    server.use(http.post(`${API}/notes`, () => HttpResponse.json({ id: "n9", title: "Nowa notatka" }, { status: 201 })));
    renderShell();
    await userEvent.click(await within(desktop()).findByRole("button", { name: "Utwórz notatkę" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/notebook/n9"));
  });

  it("logs out and forgets the account's cached data", async () => {
    me(["MEMBER"]);
    let loggedOut = false;
    server.use(
      http.post(`${API}/auth/logout`, () => {
        loggedOut = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client } = renderShell();
    await userEvent.click(await within(desktop()).findByRole("button", { name: "Wyloguj" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/"));
    expect(loggedOut).toBe(true);
    expect(client.getQueryData(["me"])).toBeUndefined();
  });
});

describe("HomeDashboard (12.5)", () => {
  it("greets the member and shows only their sections", async () => {
    me(["MEMBER"]);
    renderWithClient(<HomeDashboard />);
    expect(await screen.findByRole("heading", { level: 1, name: "Dzień dobry, Anna" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Moja praca" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Wiedza spółdzielni" })).getByRole("link", { name: /Atlas/ })).toHaveAttribute("href", "/atlas");
    expect(screen.queryByRole("region", { name: "Administracja" })).not.toBeInTheDocument();
  });

  it("asks a visitor to log in", async () => {
    server.use(http.get(`${API}/me`, () => HttpResponse.json({ detail: "unauthorized" }, { status: 401 })));
    renderWithClient(<HomeDashboard />);
    expect(await screen.findByRole("link", { name: "Zaloguj się" })).toBeInTheDocument();
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
});
