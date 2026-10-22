import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ChatView } from "@/components/chat/ChatView";
import { AskPanel } from "@/components/notebook/AskPanel";
import { DerivativesPanel } from "@/components/notebook/DerivativesPanel";
import { ApiError } from "@/lib/api";
import { isModelMissing } from "@/lib/capabilities";
import type { Derivative } from "@/lib/notebook";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function features(on: boolean) {
  return {
    wiki_answers: on,
    notebook_answers: on,
    notebook_search: on,
    promotion: on,
    image_descriptions: on,
    recognition: on,
  };
}

describe("features without a model (10.2)", () => {
  it("turns the cooperative chat off with an explanation", async () => {
    server.use(http.get(`${API}/capabilities`, () => HttpResponse.json({ features: features(false) })));
    renderWithClient(<ChatView ask={async () => ({ kind: "locked" })} />);
    expect(await screen.findByText(/Model nie jest skonfigurowany/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zapytaj" })).toBeDisabled();
  });

  it("keeps the notebook question form usable when the feature is on", async () => {
    server.use(http.get(`${API}/capabilities`, () => HttpResponse.json({ features: features(true) })));
    renderWithClient(<AskPanel />);
    await waitFor(() => expect(screen.queryByText(/Model nie jest skonfigurowany/)).toBeNull());
  });

  it("shows a waiting material as waiting, not failed", () => {
    const waiting: Derivative = {
      id: "0190f5a8-0000-7000-8000-0000000000d2",
      attachment_id: "0190f5a8-0000-7000-8000-0000000000a2",
      kind: "IMAGE_DESCRIPTION",
      status: "WAITING_FOR_MODEL",
      source: null,
      content: null,
      error: "model_not_configured:VLM",
      created_at: "2026-10-22T10:00:00Z",
      updated_at: "2026-10-22T10:00:00Z",
      injection_flags: {},
      quarantine_confirmed_at: null,
    };
    renderWithClient(<DerivativesPanel noteId="n1" derivatives={[waiting]} />);
    expect(screen.getByText("Czeka na model AI")).toBeInTheDocument();
    expect(screen.getByText(/przetworzony automatycznie/)).toBeInTheDocument();
    expect(screen.queryByText(/Nie udało się/)).toBeNull();
  });

  it("recognises the API's model_not_configured answer", () => {
    expect(isModelMissing(new ApiError(503, { detail: { error: "model_not_configured" } }))).toBe(true);
    expect(isModelMissing(new ApiError(503, { detail: { error: "model_unavailable" } }))).toBe(false);
  });
});
