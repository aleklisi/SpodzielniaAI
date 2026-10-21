import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { DomainsPanel } from "@/components/admin/atlas/DomainsPanel";
import { LabelCombobox } from "@/components/atlas/LabelCombobox";
import { LabelVocabulary } from "@/components/atlas/LabelVocabulary";
import type { CuratedLabel, Domain, Label } from "@/lib/atlas";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const BEES: Domain = { id: "d1", slug: "pszczelarstwo", name: "Pszczelarstwo", description: null, status: "ACTIVE" };
const NOW = new Date().toISOString();

function label(id: string, name: string, overrides: Partial<Label> = {}): Label {
  return {
    id,
    domain_id: BEES.id,
    slug: name.toLowerCase(),
    name,
    description: null,
    synonyms: [],
    status: "ACTIVE",
    merged_into_id: null,
    is_own_proposal: false,
    ...overrides,
  };
}

function curated(base: Label, overrides: Partial<CuratedLabel> = {}): CuratedLabel {
  return { ...base, proposed_by_name: null, created_at: NOW, ...overrides };
}

const QUEEN = label("l1", "Matka pszczela", { synonyms: ["królowa"] });
const DRONE = label("l2", "Truteń");

function Picker({ onPick }: { onPick: (l: Label) => void }) {
  const [value, setValue] = useState<Label | null>(null);
  return (
    <LabelCombobox
      domainId={BEES.id}
      value={value}
      onChange={(l) => {
        setValue(l);
        onPick(l);
      }}
    />
  );
}

describe("LabelCombobox", () => {
  it("searches the domain's labels and picks one with the keyboard", async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      http.get(`${API}/atlas/labels`, ({ request }) => {
        const params = new URL(request.url).searchParams;
        queries.push(params);
        const q = params.get("q") ?? "";
        return HttpResponse.json([QUEEN, DRONE].filter((l) => !q || l.name.toLowerCase().includes(q) || l.synonyms.some((s) => s.includes(q))));
      }),
    );
    const picked: Label[] = [];
    renderWithClient(<Picker onPick={(l) => picked.push(l)} />);
    const box = screen.getByRole("combobox", { name: "Etykieta" });
    await userEvent.type(box, "król");
    const list = await screen.findByRole("listbox");
    await waitFor(() => expect(within(list).getAllByRole("option")[0]).toHaveTextContent("Matka pszczela"));
    await userEvent.keyboard("{Enter}");
    expect(picked.map((l) => l.id)).toEqual(["l1"]);
    expect(screen.getByText("Matka pszczela", { selector: "span" })).toBeInTheDocument();
    expect(queries.every((p) => p.get("domain_id") === BEES.id)).toBe(true);
  });

  it("proposes a missing label and selects the proposal", async () => {
    let proposed: unknown = null;
    server.use(
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([])),
      http.post(`${API}/atlas/labels/proposals`, async ({ request }) => {
        proposed = await request.json();
        return HttpResponse.json(label("l9", "Miseczka mateczna", { status: "PROPOSED", is_own_proposal: true }), { status: 201 });
      }),
    );
    const picked: Label[] = [];
    renderWithClient(<Picker onPick={(l) => picked.push(l)} />);
    await userEvent.type(screen.getByRole("combobox"), "Miseczka mateczna");
    await userEvent.click(await screen.findByRole("option", { name: /Zaproponuj nową etykietę „Miseczka mateczna”/ }));
    await waitFor(() => expect(picked.map((l) => l.status)).toEqual(["PROPOSED"]));
    expect(proposed).toEqual({ domain_id: BEES.id, name: "Miseczka mateczna" });
    expect(screen.getByText(/propozycja czeka na ekspertów/)).toBeInTheDocument();
  });

  it("explains a refused proposal", async () => {
    server.use(
      http.get(`${API}/atlas/labels`, () => HttpResponse.json([])),
      http.post(`${API}/atlas/labels/proposals`, () => HttpResponse.json({ detail: { error: "rate_limited" } }, { status: 429 })),
    );
    renderWithClient(<Picker onPick={() => undefined} />);
    await userEvent.type(screen.getByRole("combobox"), "Kit");
    await userEvent.click(await screen.findByRole("option", { name: /Zaproponuj/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Limit propozycji na dziś");
  });
});

describe("LabelVocabulary", () => {
  it("lists proposals first and approves one", async () => {
    const proposal = curated(label("l5", "Kit pszczeli", { status: "PROPOSED" }), { proposed_by_name: "Ola" });
    const statuses: (string | null)[] = [];
    let approved = "";
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/curation/labels`, ({ request }) => {
        const status = new URL(request.url).searchParams.get("status");
        statuses.push(status);
        return HttpResponse.json(status === "PROPOSED" ? [proposal] : [curated(QUEEN), proposal]);
      }),
      http.post(`${API}/atlas/curation/labels/:id/approve`, ({ params }) => {
        approved = String(params.id);
        return HttpResponse.json({ ...proposal, status: "ACTIVE" });
      }),
    );
    renderWithClient(<LabelVocabulary />);
    const item = await screen.findByText("Kit pszczeli");
    expect(statuses[0]).toBe("PROPOSED");
    const card = item.closest("li")!;
    expect(within(card).getByText(/Zaproponował\(a\): Ola/)).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Zatwierdź" }));
    await waitFor(() => expect(approved).toBe("l5"));
    expect(await screen.findByText("Etykieta zatwierdzona.")).toBeInTheDocument();
  });

  it("creates a label with synonyms", async () => {
    let body: unknown = null;
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.get(`${API}/atlas/curation/labels`, () => HttpResponse.json([])),
      http.post(`${API}/atlas/curation/labels`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(QUEEN, { status: 201 });
      }),
    );
    renderWithClient(<LabelVocabulary />);
    await userEvent.click(await screen.findByRole("button", { name: "Nowa etykieta" }));
    const form = screen.getByRole("form", { name: "Etykieta" });
    await userEvent.type(within(form).getByLabelText("Nazwa"), "Matka pszczela");
    await userEvent.type(within(form).getByLabelText("Synonimy (po przecinku)"), "matka, królowa ,");
    await userEvent.click(within(form).getByRole("button", { name: "Zapisz" }));
    await waitFor(() =>
      expect(body).toEqual({ domain_id: BEES.id, name: "Matka pszczela", description: "", synonyms: ["matka", "królowa"] }),
    );
  });
});

describe("DomainsPanel", () => {
  it("creates and archives domains, explaining a duplicate", async () => {
    const created: unknown[] = [];
    const patched: unknown[] = [];
    server.use(
      http.get(`${API}/atlas/domains`, () => HttpResponse.json([BEES])),
      http.post(`${API}/admin/atlas/domains`, async ({ request }) => {
        const body = (await request.json()) as { name: string };
        created.push(body);
        if (body.name === "Pszczelarstwo") return HttpResponse.json({ detail: { error: "domain_exists" } }, { status: 409 });
        return HttpResponse.json({ ...BEES, id: "d2", name: body.name }, { status: 201 });
      }),
      http.patch(`${API}/admin/atlas/domains/:id`, async ({ request }) => {
        patched.push(await request.json());
        return HttpResponse.json({ ...BEES, status: "ARCHIVED" });
      }),
    );
    renderWithClient(<DomainsPanel />);
    const form = screen.getByRole("form", { name: "Nowa dziedzina" });
    await userEvent.type(within(form).getByLabelText("Nazwa"), "Pszczelarstwo");
    await userEvent.click(within(form).getByRole("button", { name: "Dodaj dziedzinę" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Taka dziedzina już istnieje.");
    await userEvent.clear(within(form).getByLabelText("Nazwa"));
    await userEvent.type(within(form).getByLabelText("Nazwa"), "Ogrodnictwo");
    await userEvent.click(within(form).getByRole("button", { name: "Dodaj dziedzinę" }));
    await waitFor(() => expect(created).toHaveLength(2));
    expect(created[1]).toEqual({ name: "Ogrodnictwo", description: null });

    const row = (await screen.findByText("Pszczelarstwo", { selector: "p" })).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Archiwizuj" }));
    await waitFor(() => expect(patched).toEqual([{ status: "ARCHIVED" }]));
  });
});
