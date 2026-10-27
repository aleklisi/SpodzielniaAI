import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { AtlasGallery } from "@/components/atlas/AtlasGallery";
import { AtlasPhotoPage } from "@/components/atlas/AtlasPhotoPage";
import { AtlasReviewPanel } from "@/components/review/AtlasReviewPanel";
import type { Domain, Label } from "@/lib/atlas";
import type { PhotoCard, PhotoDetail, QueuePhoto, ReviewPhoto } from "@/lib/atlas-gallery";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const BEES: Domain = { id: "d1", slug: "pszczelarstwo", name: "Pszczelarstwo", description: null, status: "ACTIVE" };
const QUEEN: Label = {
  id: "l1",
  domain_id: "d1",
  slug: "matka-pszczela",
  name: "Matka pszczela",
  description: null,
  synonyms: [],
  status: "ACTIVE",
  merged_into_id: null,
  is_own_proposal: false,
};
const NOW = "2026-10-21T10:00:00Z";

function card(id: string, author: string | null): PhotoCard {
  return { id, domain_id: "d1", caption: "Ramka 3", labels: ["Matka pszczela"], author_name: author, verified_at: NOW, width: 1200, height: 800 };
}

describe("AtlasGallery", () => {
  it("lists approved photos with the author's choice and filters by domain, label and text", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([QUEEN])),
      http.get(`${API}/atlas/photos`, ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ items: [card("p1", "Alicja"), card("p2", null)], total: 2, offset: 0, limit: 24 });
      }),
    );
    renderWithClient(<AtlasGallery />);
    const list = await screen.findByRole("list", { name: "Zdjęcia atlasu" });
    const items = await within(list).findAllByRole("link");
    expect(items[0]).toHaveAttribute("href", "/atlas/p1");
    expect(items[0]).toHaveTextContent("Alicja");
    expect(items[1]).toHaveTextContent("Członek spółdzielni");
    expect(within(items[0]).getByRole("img")).toHaveAttribute("src", "/api/atlas/images/p1/image?size=thumb");

    await userEvent.selectOptions(screen.getByLabelText("Dziedzina"), "d1");
    await waitFor(() => expect(screen.getByLabelText("Etykieta")).toBeEnabled());
    await userEvent.selectOptions(screen.getByLabelText("Etykieta"), "l1");
    await userEvent.type(screen.getByLabelText("Szukaj"), "matka");
    await waitFor(() => {
      const last = queries.at(-1)!;
      expect([last.get("domain_id"), last.get("label_id"), last.get("q")]).toEqual(["d1", "l1", "matka"]);
    });
  });
});

describe("AtlasPhotoPage", () => {
  const detail: PhotoDetail = {
    id: "p2",
    domain_id: "d1",
    domain_name: "Pszczelarstwo",
    status: "APPROVED",
    consent_status: "ACTIVE",
    caption: "Ramka 3",
    width: 1200,
    height: 800,
    regions: [{ id: "r1", label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona" }],
    author_name: null,
    is_own: false,
    verified_at: NOW,
    reviewers: ["Ewa", "Jan"],
  };

  it("shows marks, the anonymous author and the reviewers", async () => {
    server.use(http.get(`${API}/atlas/photos/p2`, () => HttpResponse.json(detail)));
    renderWithClient(<AtlasPhotoPage imageId="p2" />);
    expect(await screen.findByRole("heading", { name: "Matka pszczela" })).toBeInTheDocument();
    expect(screen.getByText(/Autor: Członek spółdzielni/)).toHaveTextContent("eksperci: Ewa, Jan");
    expect(screen.getByRole("button", { name: /1\. Matka pszczela/ })).toHaveTextContent("znaczona");
    expect(screen.getByRole("button", { name: "Oznaczenie 1: Matka pszczela" })).toBeInTheDocument();
  });

  it("explains the coverage throttle", async () => {
    server.use(http.get(`${API}/atlas/photos/p2`, () => HttpResponse.json({ detail: "throttled" }, { status: 429, headers: { "Retry-After": "60" } })));
    renderWithClient(<AtlasPhotoPage imageId="p2" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("bardzo dużo zdjęć atlasu");
  });
});

describe("AtlasReviewPanel", () => {
  const queued: QueuePhoto = {
    id: "p3",
    domain_id: "d1",
    version: 1,
    regions: 1,
    proposed_labels: 0,
    privacy_flags: [],
    privacy_check: "ok",
    approvals: 0,
    quorum: 2,
    is_own: false,
    submitted_at: NOW,
  };
  const photo: ReviewPhoto = {
    id: "p3",
    domain_id: "d1",
    domain_name: "Pszczelarstwo",
    status: "IN_REVIEW",
    version: 1,
    width: 1200,
    height: 800,
    caption: "Ramka",
    regions: [{ id: "r1", label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: null, corrected: false, by_reviewer: false }],
    privacy_flags: [],
    privacy_check: "ok",
    approvals: 0,
    quorum: 2,
    is_own: false,
    history: [],
  };

  function mount(detail: ReviewPhoto, onDecision?: (body: unknown) => void, onRegions?: (body: unknown) => void) {
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([QUEEN])),
      http.get(`${API}/atlas/review/queue`, () => HttpResponse.json([queued])),
      http.get(`${API}/atlas/review/p3`, () => HttpResponse.json(detail)),
      http.post(`${API}/atlas/review/p3/decision`, async ({ request }) => {
        onDecision?.(await request.json());
        return HttpResponse.json({ id: "p3", previous: "IN_REVIEW", status: "IN_REVIEW", version: 1, approvals: 1, quorum: 2 });
      }),
      http.put(`${API}/atlas/review/p3/regions`, async ({ request }) => {
        onRegions?.(await request.json());
        return HttpResponse.json({ ...detail, version: 2 });
      }),
    );
    renderWithClient(<AtlasReviewPanel />);
  }

  it("approves the loaded version and needs a comment to send back", async () => {
    const decisions: unknown[] = [];
    mount(photo, (b) => decisions.push(b));
    await userEvent.click(await screen.findByRole("button", { name: /Pszczelarstwo/ }));
    const form = await screen.findByRole("form", { name: "Decyzja" });
    await userEvent.click(within(form).getByRole("button", { name: "Zapisz decyzję" }));
    await waitFor(() => expect(decisions).toEqual([{ decision: "APPROVE", comment: null, version: 1 }]));
    await userEvent.click(within(form).getByLabelText("Poproś o poprawki"));
    expect(within(form).getByRole("button", { name: "Zapisz decyzję" })).toBeDisabled();
    await userEvent.type(within(form).getByLabelText(/Komentarz dla autora/), "Zaznacz całą matkę");
    await userEvent.click(within(form).getByRole("button", { name: "Zapisz decyzję" }));
    await waitFor(() => expect(decisions.at(-1)).toEqual({ decision: "REQUEST_CHANGES", comment: "Zaznacz całą matkę", version: 1 }));
  });

  it("saves corrections with the ids of existing marks before deciding", async () => {
    const corrections: unknown[] = [];
    mount(photo, undefined, (b) => corrections.push(b));
    await userEvent.click(await screen.findByRole("button", { name: /Pszczelarstwo/ }));
    await userEvent.type(await screen.findByLabelText("Opis oznaczenia 1"), "młoda");
    expect(screen.getByRole("button", { name: "Zapisz decyzję" })).toBeDisabled(); // corrections first
    await userEvent.click(screen.getByRole("button", { name: "Zapisz poprawki" }));
    await waitFor(() =>
      expect(corrections).toEqual([{ version: 1, regions: [{ id: "r1", label_id: "l1", x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "młoda" }] }]),
    );
  });

  it("is read-only for the expert's own photo and points to pending labels", async () => {
    mount({
      ...photo,
      is_own: true,
      regions: [{ ...photo.regions[0], label: { id: "l9", name: "Kit", status: "PROPOSED" } }],
    });
    await userEvent.click(await screen.findByRole("button", { name: /Pszczelarstwo/ }));
    expect(await screen.findByText(/To Twoje zdjęcie/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Decyzja" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "słowniku etykiet" })).toHaveAttribute("href", "/review/labels");
  });
});

describe("in English (14.3)", () => {
  it("lists the photos with English labels; the labels themselves stay as written", async () => {
    setTestLocale("en");
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([QUEEN])),
      http.get(`${API}/atlas/photos`, () => HttpResponse.json({ items: [card("p1", "Alicja"), card("p2", null)], total: 2, offset: 0, limit: 24 })),
    );
    renderWithClient(<AtlasGallery />);
    const items = await within(await screen.findByRole("list", { name: "Atlas photos" })).findAllByRole("link");
    expect(items[1]).toHaveTextContent("Cooperative member");
    expect(screen.getByLabelText("Domain")).toBeInTheDocument();
    expect(screen.getByLabelText("Label")).toBeInTheDocument();
    expect(screen.getByLabelText("Search")).toHaveAttribute("placeholder", "e.g. queen, queen cell");
  });
});
