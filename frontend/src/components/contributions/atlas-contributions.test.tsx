import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { AtlasContributions } from "@/components/contributions/AtlasContributions";
import type { MyPhoto } from "@/lib/atlas-submissions";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const PHOTO: MyPhoto = {
  id: "p1",
  status: "APPROVED",
  consent_status: "ACTIVE",
  labels: ["Matka pszczela"],
  caption: "Ramka 3",
  show_author: false,
  allow_export: false,
  review_alert: null,
  submitted_at: "2026-10-21T10:00:00Z",
  verified_at: "2026-10-21T11:00:00Z",
};

describe("AtlasContributions", () => {
  it("suspends a photo and changes the author's choices", async () => {
    const calls: string[] = [];
    let patched: unknown = null;
    server.use(
      http.get(`${API}/me/contributions/atlas`, () => HttpResponse.json([PHOTO])),
      http.post(`${API}/me/contributions/atlas/p1/suspend`, () => {
        calls.push("suspend");
        return HttpResponse.json({ ...PHOTO, consent_status: "SUSPENDED" });
      }),
      http.patch(`${API}/me/contributions/atlas/p1`, async ({ request }) => {
        patched = await request.json();
        return HttpResponse.json({ ...PHOTO, show_author: true });
      }),
    );
    renderWithClient(<AtlasContributions />);
    expect(await screen.findByText("Udostępnione")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Wstrzymaj" }));
    await waitFor(() => expect(calls).toEqual(["suspend"]));
    await userEvent.click(screen.getByLabelText(/Pokazuj moje imię i nazwisko/));
    await waitFor(() => expect(patched).toEqual({ show_author: true }));
  });

  it("revokes only after typing REVOKE and sends a stale login to step-up", async () => {
    server.use(
      http.get(`${API}/me/contributions/atlas`, () => HttpResponse.json([PHOTO])),
      http.post(`${API}/me/contributions/atlas/p1/revoke`, () =>
        HttpResponse.json({ detail: { error: "reauthentication_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 }),
      ),
    );
    renderWithClient(<AtlasContributions />);
    await userEvent.click(await screen.findByRole("button", { name: "Wycofaj trwale" }));
    const dialog = screen.getByRole("dialog", { name: "Wycofaj zdjęcie trwale" });
    const confirm = within(dialog).getByRole("button", { name: "Wycofaj trwale" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Potwierdzenie"), "REVOKE");
    await userEvent.click(confirm);
    expect(await within(dialog).findByText("Potwierdź swoją tożsamość")).toBeInTheDocument();
  });

  it("shows a revoked photo as a tombstone without actions", async () => {
    server.use(http.get(`${API}/me/contributions/atlas`, () => HttpResponse.json([{ ...PHOTO, consent_status: "REVOKED", caption: null }])));
    renderWithClient(<AtlasContributions />);
    expect(await screen.findByText("Wycofane trwale")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Wstrzymaj" })).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
