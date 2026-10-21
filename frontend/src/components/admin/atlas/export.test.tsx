import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ExportPanel } from "@/components/admin/atlas/ExportPanel";
import type { AtlasExport } from "@/lib/atlas-export";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const BEES = { id: "d1", slug: "pszczelarstwo", name: "Pszczelarstwo", description: null, status: "ACTIVE" };

function exported(overrides: Partial<AtlasExport> = {}): AtlasExport {
  return {
    id: "e1",
    status: "READY",
    format: "COCO",
    domain_id: "d1",
    domain_name: "Pszczelarstwo",
    created_by_name: "Admin",
    image_count: 3,
    region_count: 7,
    revoked_since: 0,
    manifest_sha256: "ab".repeat(32),
    expires_at: "2026-10-22T10:00:00Z",
    downloaded_at: null,
    created_at: "2026-10-21T10:00:00Z",
    error: null,
    ...overrides,
  };
}

describe("ExportPanel", () => {
  it("explains that the operator disabled exports", async () => {
    server.use(
      http.get(`${API}/admin/atlas/exports`, () => HttpResponse.json({ enabled: false, exports: [] })),
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
    );
    renderWithClient(<ExportPanel />);
    expect(await screen.findByText(/Eksport jest wyłączony/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Nowy eksport" })).not.toBeInTheDocument();
  });

  it("creates an export and offers the one-time download link", async () => {
    let body: unknown = null;
    let list: AtlasExport[] = [];
    server.use(
      http.get(`${API}/admin/atlas/exports`, () => HttpResponse.json({ enabled: true, exports: list })),
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([])),
      http.post(`${API}/admin/atlas/exports`, async ({ request }) => {
        body = await request.json();
        list = [exported({ revoked_since: 1 })];
        return HttpResponse.json({ export: list[0], download_token: "t".repeat(43) }, { status: 202 });
      }),
    );
    renderWithClient(<ExportPanel />);
    const form = await screen.findByRole("form", { name: "Nowy eksport" });
    await within(form).findByRole("option", { name: "Pszczelarstwo" });
    await userEvent.selectOptions(within(form).getByLabelText("Dziedzina"), "d1");
    await userEvent.click(within(form).getByLabelText("YOLO"));
    await userEvent.click(within(form).getByRole("button", { name: "Utwórz eksport" }));
    await waitFor(() => expect(body).toEqual({ format: "YOLO", domain_id: "d1", label_ids: null, min_verified_at: null }));
    const link = await screen.findByRole("link", { name: "Pobierz (jednorazowo)" });
    expect(link).toHaveAttribute("href", `/api/admin/atlas/exports/e1/download?token=${"t".repeat(43)}`);
    expect(screen.getByText("wycofane od eksportu: 1")).toBeInTheDocument();
  });

  it("asks for a fresh login", async () => {
    server.use(
      http.get(`${API}/admin/atlas/exports`, () => HttpResponse.json({ enabled: true, exports: [] })),
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([])),
      http.post(`${API}/admin/atlas/exports`, () =>
        HttpResponse.json({ detail: { error: "reauthentication_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 }),
      ),
    );
    renderWithClient(<ExportPanel />);
    const form = await screen.findByRole("form", { name: "Nowy eksport" });
    await within(form).findByRole("option", { name: "Pszczelarstwo" });
    await userEvent.selectOptions(within(form).getByLabelText("Dziedzina"), "d1");
    await userEvent.click(within(form).getByRole("button", { name: "Utwórz eksport" }));
    expect(await screen.findByText("Potwierdź swoją tożsamość")).toBeInTheDocument();
  });
});
