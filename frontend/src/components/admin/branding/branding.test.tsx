import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { BrandingConsole } from "@/components/admin/branding/BrandingConsole";
import { BrandMark } from "@/components/shell/BrandMark";
import type { AdminBranding, Preview } from "@/lib/branding-admin";
import { BrandingProvider } from "@/lib/branding-context";
import { brandingCss, DEFAULT_BRANDING, sanitizeBranding, TOKENS } from "@/lib/branding-css";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  server.resetHandlers();
  for (const token of TOKENS) document.documentElement.style.removeProperty(`--${token}`);
});
afterAll(() => server.close());

const tokens = (value: string) => Object.fromEntries(TOKENS.map((t) => [t, value]));
const PALETTE = { light: tokens("#1f6f4a"), dark: tokens("#438e67") };

const ADMIN: AdminBranding = {
  org_name: "Spółdzielnia AI",
  custom_name: false,
  version: 0,
  palette: null,
  logo_url: null,
  favicon_urls: null,
  brand: null,
  accent: null,
  updated_at: null,
};

const PREVIEW: Preview = {
  light: { ...tokens("#82781a") },
  dark: { ...tokens("#fff59d") },
  adjusted: ["brand"],
  checks: [
    { mode: "light", token: "primary-contrast", against: "primary", ratio: 4.61, required: 4.5 },
    { mode: "dark", token: "primary", against: "bg", ratio: 15.2, required: 3 },
  ],
};

describe("brandingCss (rule R2)", () => {
  it("writes the six tokens for both modes, overriding globals.css", () => {
    const css = brandingCss(PALETTE)!;
    expect(css).toContain(":root:root{--primary:#1f6f4a;--primary-strong:#1f6f4a;");
    expect(css).toContain("@media (prefers-color-scheme: dark){:root:root{--primary:#438e67;");
    expect(css).not.toMatch(/[<>"'\\]/);
  });

  it.each([
    ["a value that is not #rrggbb", { ...PALETTE, light: { ...PALETTE.light, primary: "#000;}body{display:none" } }],
    ["a missing token", { ...PALETTE, dark: { primary: "#000000" } }],
    ["a missing mode", { light: PALETTE.light }],
    ["an upper-case hex", { ...PALETTE, light: { ...PALETTE.light, accent: "#ABCDEF" } }],
  ])("drops the whole palette for %s", (_case, palette) => {
    expect(brandingCss(palette as never)).toBeNull();
  });

  it("ignores tokens it does not know", () => {
    const css = brandingCss({ ...PALETTE, light: { ...PALETTE.light, bg: "#000000" } })!;
    expect(css).not.toContain("--bg");
  });
});

describe("sanitizeBranding", () => {
  it("keeps only same-origin logo files of the current version", () => {
    const branding = sanitizeBranding({
      ...DEFAULT_BRANDING,
      org_name: "Pasieka",
      logo_url: "/api/branding/files/4/logo.png",
      favicon_urls: { "32": "/api/branding/files/4/favicon-32.png", "180": "/api/branding/files/4/favicon-180.png" },
    });
    expect(branding.logo_url).toBe("/api/branding/files/4/logo.png");
    for (const evil of ["https://evil.example/logo.png", "javascript:alert(1)", "/api/branding/files/4/../../x.png", "//evil/x.png"]) {
      expect(sanitizeBranding({ ...DEFAULT_BRANDING, logo_url: evil }).logo_url).toBeNull();
    }
  });

  it("falls back to the default look for anything unexpected", () => {
    expect(sanitizeBranding(null)).toEqual(DEFAULT_BRANDING);
    expect(sanitizeBranding({ org_name: "   " }).org_name).toBe("Spółdzielnia AI");
    expect(sanitizeBranding({ org_name: "x".repeat(200) }).org_name).toHaveLength(60);
  });
});

describe("BrandMark", () => {
  it("shows the hexagon without a logo, and the logo (decorative) with one", () => {
    const { container, rerender } = renderWithClient(<BrandMark />);
    expect(container.querySelector("svg")).not.toBeNull();
    rerender(
      <BrandingProvider
        branding={{
          ...DEFAULT_BRANDING,
          logo_url: "/api/branding/files/2/logo.png",
          favicon_urls: { "32": "/api/branding/files/2/favicon-32.png", "180": "/api/branding/files/2/favicon-180.png" },
        }}
      >
        <BrandMark rail />
      </BrandingProvider>,
    );
    const images = container.querySelectorAll("img");
    expect([...images].map((i) => i.getAttribute("src"))).toEqual(["/api/branding/files/2/favicon-180.png", "/api/branding/files/2/logo.png"]);
    expect([...images].every((i) => i.getAttribute("alt") === "")).toBe(true);
  });
});

describe("BrandingConsole (13.5)", () => {
  it("previews the server's palette, says what was adjusted, and saves", async () => {
    let saved: unknown;
    server.use(
      http.get(`${API}/admin/branding`, () => HttpResponse.json(ADMIN)),
      http.post(`${API}/admin/branding/preview`, () => HttpResponse.json(PREVIEW)),
      http.put(`${API}/admin/branding`, async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ ...ADMIN, org_name: "Pasieka Kowalscy", custom_name: true, version: 1, palette: PALETTE, brand: "#fff59d", accent: "#6b4f96" });
      }),
    );
    renderWithClient(<BrandingConsole />);
    await userEvent.type(await screen.findByLabelText("Nazwa organizacji"), "Pasieka Kowalscy");
    await userEvent.click(screen.getByRole("checkbox", { name: "Własne kolory" }));
    const brand = screen.getByLabelText("Kolor marki");
    await userEvent.clear(brand);
    await userEvent.type(brand, "#fff59d");

    expect(await screen.findByText(/Kolor marki przyciemniony/)).toBeInTheDocument();
    // The light panel shows the derived colours through the CSSOM (no style attribute in HTML).
    const light = screen.getByTestId("preview-light");
    await waitFor(() => expect(light.style.getPropertyValue("--primary")).toBe("#82781a"));
    expect(screen.getByTestId("preview-dark").style.getPropertyValue("--bg")).toBe("#15130f");
    expect(within(screen.getByRole("region", { name: "Podgląd" })).getAllByText("Pasieka Kowalscy")).toHaveLength(2);

    await userEvent.click(screen.getByRole("button", { name: "Zapisz" }));
    await waitFor(() => expect(saved).toEqual({ org_name: "Pasieka Kowalscy", brand: "#fff59d", accent: "#6b4f96" }));
    // Applied to this tab at once.
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--primary")).toBe("#1f6f4a"));
  });

  it("explains a refused colour and an invalid hex", async () => {
    server.use(
      http.get(`${API}/admin/branding`, () => HttpResponse.json({ ...ADMIN, brand: "#1f6f4a", accent: "#c2410c", palette: PALETTE })),
      http.post(`${API}/admin/branding/preview`, () =>
        HttpResponse.json({ detail: { error: "contrast_unreachable", which: "brand", mode: "light" } }, { status: 422 }),
      ),
    );
    renderWithClient(<BrandingConsole />);
    expect(await screen.findByRole("alert")).toHaveTextContent("tekst nie byłby czytelny");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
    const accent = screen.getByLabelText("Kolor akcentu");
    await userEvent.clear(accent);
    await userEvent.type(accent, "red");
    expect(await screen.findByRole("alert")).toHaveTextContent("#rrggbb");
  });

  it("uploads a logo, reports a refused SVG, and resets after confirmation", async () => {
    const withLogo = { ...ADMIN, version: 2, logo_url: "/api/branding/files/2/logo.png", favicon_urls: { "32": "a", "180": "b" } };
    let reset = false;
    let uploads = 0;
    server.use(
      http.get(`${API}/admin/branding`, () => HttpResponse.json(ADMIN)),
      // The first upload (the SVG) is refused, the second accepted.
      http.post(`${API}/admin/branding/logo`, () =>
        ++uploads === 1 ? HttpResponse.json({ detail: { error: "type_not_allowed" } }, { status: 415 }) : HttpResponse.json(withLogo),
      ),
      http.post(`${API}/admin/branding/reset`, () => {
        reset = true;
        return HttpResponse.json({ ...ADMIN, version: 3 });
      }),
    );
    renderWithClient(<BrandingConsole />);
    const input = await screen.findByLabelText("Plik logo");
    // Without own colours each panel shows its own mode's defaults, never the page's light ones.
    await waitFor(() => expect(screen.getByTestId("preview-dark").style.getPropertyValue("--primary")).toBe("#e0a43a"));
    expect(screen.getByTestId("preview-light").style.getPropertyValue("--primary")).toBe("#9a6414");
    await userEvent.upload(input, new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" }), { applyAccept: false });
    expect(await screen.findByText(/SVG nie jest obsługiwany/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Nazwa organizacji"), "Niezapisana nazwa");
    await userEvent.upload(input, new File(["png"], "logo.png", { type: "image/png" }));
    expect(await screen.findByRole("img", { name: "Logo: Spółdzielnia AI" })).toHaveAttribute("src", "/api/branding/files/2/logo.png");
    // A new logo (a new version) keeps what the admin typed but has not saved yet.
    expect(screen.getByLabelText("Nazwa organizacji")).toHaveValue("Niezapisana nazwa");

    await userEvent.click(screen.getByRole("button", { name: "Przywróć domyślne" }));
    const dialog = screen.getByRole("dialog", { name: "Przywrócić domyślny wygląd?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Przywróć" }));
    await waitFor(() => expect(reset).toBe(true));
    await waitFor(() => expect(screen.getByLabelText("Nazwa organizacji")).toHaveValue(""));
  });

  it("asks for MFA when the API wants a step-up", async () => {
    vi.stubGlobal("location", { ...window.location, pathname: "/admin/branding" });
    server.use(
      http.get(`${API}/admin/branding`, () =>
        HttpResponse.json({ detail: { error: "mfa_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 }),
      ),
    );
    renderWithClient(<BrandingConsole />);
    expect(await screen.findByRole("link", { name: "Zaloguj ponownie z MFA" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});

describe("in English (14.4)", () => {
  it("previews and explains the adjustment in English", async () => {
    setTestLocale("en");
    server.use(
      http.get(`${API}/admin/branding`, () => HttpResponse.json(ADMIN)),
      http.post(`${API}/admin/branding/preview`, () => HttpResponse.json(PREVIEW)),
    );
    renderWithClient(<BrandingConsole />);
    expect(await screen.findByRole("heading", { name: "Appearance", level: 1 })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Custom colours" }));
    const brand = screen.getByLabelText("Brand colour");
    await userEvent.clear(brand);
    await userEvent.type(brand, "#fff59d");
    expect(await screen.findByText(/Brand colour darkened/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Preview" })).getAllByText("Notebook")).toHaveLength(2);
  });
});
