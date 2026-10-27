import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { IndexPanel } from "@/components/admin/llm/IndexPanel";
import { ProcessorsBanner } from "@/components/privacy/ProcessorsBanner";
import { ProcessorsPage } from "@/components/privacy/ProcessorsPage";
import { eta, type IndexGeneration, type IndexState } from "@/lib/llm-admin";
import type { Processors } from "@/lib/processors";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ME = { id: "u1", display_name: "Anna", email: null, roles: ["MEMBER"], mfa_satisfied: false };

const PROCESSORS: Processors = {
  processors: [
    {
      name: "local",
      host: "llm",
      on_server: true,
      region: "serwer spółdzielni",
      retention: "brak przechowywania",
      contract_ref: "konfiguracja serwera",
      cleared_since: "2026-10-22T10:00:00Z",
      purposes: ["PRIVATE_NOTE", "EMBED"],
    },
    {
      name: "mistral",
      host: "api.mistral.ai",
      on_server: false,
      region: "UE (Francja)",
      retention: "30 dni",
      contract_ref: "DPA 2026/07",
      cleared_since: "2026-10-23T09:00:00Z",
      purposes: [],
    },
  ],
  built_in: ["OCR"],
  banner: true,
};

describe("where private data is processed (P4)", () => {
  it("lists each cleared provider with region, retention, contract and what it processes", async () => {
    server.use(http.get(`${API}/privacy/processors`, () => HttpResponse.json(PROCESSORS)));
    renderWithClient(<ProcessorsPage />);

    const local = await screen.findByLabelText("Dostawca local");
    expect(local).toHaveTextContent("Serwer spółdzielni");
    expect(local).toHaveTextContent("odpowiedzi na pytania do Twojego notatnika");
    const cloud = screen.getByLabelText("Dostawca mistral");
    expect(cloud).toHaveTextContent("Chmura");
    expect(cloud).toHaveTextContent("UE (Francja)");
    expect(cloud).toHaveTextContent("30 dni");
    expect(cloud).toHaveTextContent("DPA 2026/07");
    expect(cloud).toHaveTextContent("obecnie nie przetwarza Twoich danych");
    expect(screen.getByLabelText("Modele wbudowane")).toHaveTextContent("odczyt zeskanowanych dokumentów");
  });

  it("says so when no provider is cleared", async () => {
    server.use(
      http.get(`${API}/privacy/processors`, () => HttpResponse.json({ processors: [], built_in: [], banner: false })),
    );
    renderWithClient(<ProcessorsPage />);
    expect(await screen.findByText(/funkcje AI na Twoich danych są wyłączone/)).toBeInTheDocument();
  });

  it("shows the banner once and remembers the acknowledgement on the server", async () => {
    let seen = false;
    server.use(
      http.get(`${API}/me`, () => HttpResponse.json(ME)),
      http.get(`${API}/privacy/processors`, () => HttpResponse.json(PROCESSORS)),
      http.post(`${API}/privacy/processors/seen`, () => {
        seen = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithClient(<ProcessorsBanner />);
    const banner = await screen.findByRole("status");
    expect(within(banner).getByRole("link", { name: "Gdzie przetwarzane są Twoje dane" })).toHaveAttribute("href", "/privacy");
    await userEvent.click(within(banner).getByRole("button", { name: "Rozumiem" }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(seen).toBe(true);
  });
});

function generation(overrides: Partial<IndexGeneration>): IndexGeneration {
  return {
    number: 1,
    status: "SERVING",
    model: "local/qwen3-embedding",
    dimension: 2560,
    total: 0,
    done: 0,
    failed: 0,
    created_at: "2026-10-23T10:00:00Z",
    serving_since: "2026-10-23T10:00:00Z",
    retired_at: null,
    drop_after: null,
    dropped: false,
    ...overrides,
  };
}

describe("index versions (re-embed progress)", () => {
  it("estimates the time left from the pace so far", () => {
    const g = generation({ status: "BUILDING", total: 100, done: 25, created_at: "2026-10-23T10:00:00Z" });
    expect(eta(g, Date.parse("2026-10-23T10:01:00Z"))).toBe(180);
    expect(eta(generation({ status: "BUILDING", total: 100, done: 0 }))).toBeNull();
  });

  it("shows the build's progress and cancels it", async () => {
    let cancelled: string | null = null;
    const state: IndexState = {
      generations: [
        generation({ number: 2, status: "BUILDING", model: "mistral/mistral-embed", dimension: 1024, total: 40, done: 10, serving_since: null }),
        generation({}),
      ],
      items: 40,
      reembed_per_minute: 600,
      atlas_generations: [generation({ number: 3, model: "Wbudowany (SigLIP)", dimension: 768 })],
      atlas_items: 7,
    };
    server.use(
      http.get(`${API}/admin/llm/index`, () => HttpResponse.json(state)),
      http.post(`${API}/admin/llm/index/:number/cancel`, ({ params }) => {
        cancelled = String(params.number);
        return HttpResponse.json(state);
      }),
    );
    renderWithClient(<IndexPanel />);

    const building = await screen.findByLabelText("Wersja 2");
    expect(building).toHaveTextContent("W budowie");
    expect(building).toHaveTextContent("10 / 40 (25%)");
    expect(within(building).getByLabelText("Postęp wersji 2")).toHaveAttribute("value", "25");
    expect(screen.getByLabelText("Wersja 1")).toHaveTextContent("Używana");
    expect(screen.getByLabelText("Wersja 3")).toHaveTextContent("Wbudowany (SigLIP)");
    expect(screen.getByText(/przetworzy ponownie 7 zdjęć/)).toBeInTheDocument();

    await userEvent.click(within(building).getByRole("button", { name: "Anuluj budowę" }));
    await waitFor(() => expect(cancelled).toBe("2"));
  });
});

describe("in English (14.3)", () => {
  it("explains where private data goes", async () => {
    setTestLocale("en");
    server.use(http.get(`${API}/privacy/processors`, () => HttpResponse.json(PROCESSORS)));
    renderWithClient(<ProcessorsPage />);
    expect(await screen.findByRole("heading", { name: "Where your data is processed" })).toBeInTheDocument();
    const local = await screen.findByLabelText("Provider local");
    expect(local).toHaveTextContent("Cooperative's server");
    expect(local).toHaveTextContent("answering questions to your notebook");
    const cloud = screen.getByLabelText("Provider mistral");
    expect(cloud).toHaveTextContent("Cloud");
    expect(cloud).toHaveTextContent("Cleared since");
    expect(cloud).toHaveTextContent("23 October 2026");
    expect(screen.getByLabelText("Built-in models")).toHaveTextContent("reading scanned documents");
  });
});
