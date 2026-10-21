import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ImageAnnotator } from "@/components/annotator/ImageAnnotator";
import { PhotoEditor } from "@/components/notebook/PhotoEditor";
import { PhotosPanel } from "@/components/notebook/PhotosPanel";
import type { Domain, Label } from "@/lib/atlas";
import { clampBox, type Mark, type Photo } from "@/lib/photos";
import type { Attachment } from "@/lib/upload";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom has no PointerEvent: a MouseEvent with a pointer id is all the annotator reads.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  window.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
}

/** The annotator surface measures 1000 × 500 px at the page origin. */
function sizeSurface() {
  const surface = screen.getByTestId("annotator-surface");
  surface.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON: () => ({}) });
  return surface;
}

function mark(key: string, overrides: Partial<Mark> = {}): Mark {
  return { key, label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "", source: "USER", ...overrides };
}

function Harness({ initial = [] as Mark[], readOnly = false, onMarks }: { initial?: Mark[]; readOnly?: boolean; onMarks?: (m: Mark[]) => void }) {
  const [marks, setMarks] = useState(initial);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <ImageAnnotator
      src="/x.jpg"
      alt="ul"
      aspect={2}
      marks={marks}
      selected={selected}
      onSelect={setSelected}
      readOnly={readOnly}
      onChange={(m) => {
        setMarks(m);
        onMarks?.(m);
      }}
    />
  );
}

describe("ImageAnnotator", () => {
  it("draws a box by dragging and ignores a plain click", () => {
    let latest: Mark[] = [];
    render(<Harness onMarks={(m) => (latest = m)} />);
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    expect(latest).toHaveLength(1);
    const [box] = latest;
    expect([box.x, box.y, box.w, box.h].map((v) => Number(v.toFixed(3)))).toEqual([0.1, 0.2, 0.3, 0.4]);
    expect(box.label).toBeNull();
    expect(screen.getByRole("button", { name: "Oznaczenie 1: bez etykiety" })).toBeInTheDocument();

    // A click without dragging leaves no box behind.
    fireEvent.pointerDown(surface, { button: 0, clientX: 800, clientY: 400, pointerId: 2 });
    fireEvent.pointerUp(surface, { clientX: 801, clientY: 400, pointerId: 2 });
    expect(latest).toHaveLength(1);
  });

  it("moves a box and resizes it with a handle, staying inside the photo", () => {
    let latest: Mark[] = [];
    render(<Harness initial={[mark("a")]} onMarks={(m) => (latest = m)} />);
    const surface = sizeSurface();
    const box = screen.getByRole("button", { name: /Oznaczenie 1/ });
    // Drag the box far right: it stops at the edge (x + w = 1).
    fireEvent.pointerDown(box, { button: 0, clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 2000, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(Number((latest[0].x + latest[0].w).toFixed(6))).toBe(1);
    expect(latest[0].y).toBeCloseTo(0.2);

    // The selected box shows handles; the south-east one resizes.
    const handle = box.querySelector('[data-handle="se"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, clientY: 300, pointerId: 3 });
    fireEvent.pointerMove(surface, { clientX: 1000, clientY: 450, pointerId: 3 });
    fireEvent.pointerUp(surface, { pointerId: 3 });
    expect(latest[0].y + latest[0].h).toBeCloseTo(0.9);
  });

  it("is operable with the keyboard", async () => {
    let latest: Mark[] = [];
    render(<Harness initial={[mark("a"), mark("b", { x: 0.5 })]} onMarks={(m) => (latest = m)} />);
    const first = screen.getByRole("button", { name: /Oznaczenie 1/ });
    act(() => first.focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(latest[0].x).toBeCloseTo(0.11);
    await userEvent.keyboard("{Shift>}{ArrowDown}{/Shift}");
    expect(latest[0].y).toBeCloseTo(0.202);
    await userEvent.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(latest[0].w).toBeCloseTo(0.31);
    await userEvent.keyboard("{Delete}");
    expect(latest.map((m) => m.key)).toEqual(["b"]);
  });

  it("changes nothing when read-only", () => {
    let calls = 0;
    render(<Harness initial={[mark("a")]} readOnly onMarks={() => calls++} />);
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(calls).toBe(0);
  });

  it("clamps boxes to the photo", () => {
    expect(clampBox({ x: 0.9, y: -0.2, w: 0.3, h: 0.0001 })).toEqual({ x: 0.7, y: 0, w: 0.3, h: 0.005 });
  });
});

// --- PhotoEditor --------------------------------------------------------------------------------

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

function photo(overrides: Partial<Photo> = {}): Photo {
  return {
    attachment_id: "a1",
    note_id: "n1",
    mime_type: "image/jpeg",
    width: 3000,
    height: 2000,
    domain_id: "d1",
    caption: "Ramka 3",
    version: 1,
    regions: [{ id: "r1", label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona", source: "USER" }],
    updated_at: null,
    ...overrides,
  };
}

function mountEditor(initial: Photo, put: (body: unknown) => Response | Promise<Response>, domains: Domain[] = [BEES]) {
  server.use(
    http.get(`${API}/atlas/domains`, () => HttpResponse.json(domains)),
    http.get(`${API}/atlas/labels`, () => HttpResponse.json([QUEEN])),
    http.get(`${API}/notes/n1/attachments/a1/annotations`, () => HttpResponse.json(initial)),
    http.get(`${API}/notes/n1`, () => HttpResponse.json({})),
    http.put(`${API}/notes/n1/attachments/a1/annotations`, async ({ request }) => put(await request.json())),
  );
  renderWithClient(<PhotoEditor noteId="n1" attachmentId="a1" />);
}

describe("PhotoEditor", () => {
  it("loads marks and saves the full set with the loaded version", async () => {
    let body: unknown = null;
    mountEditor(photo(), (b) => {
      body = b;
      return HttpResponse.json(photo({ version: 2, caption: "Ramka 3, ul 7" }));
    });
    const caption = await screen.findByLabelText("Opis zdjęcia");
    expect(caption).toHaveValue("Ramka 3");
    expect(screen.getByLabelText("Opis oznaczenia")).toHaveValue("znaczona");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled(); // nothing changed yet
    await userEvent.type(caption, ", ul 7");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz" }));
    await waitFor(() =>
      expect(body).toEqual({
        domain_id: "d1",
        caption: "Ramka 3, ul 7",
        version: 1,
        regions: [{ label_id: "l1", x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona", source: "USER" }],
      }),
    );
    expect(await screen.findByText("Zapisano oznaczenia.")).toBeInTheDocument();
  });

  it("offers a reload when the photo changed elsewhere", async () => {
    mountEditor(photo(), () => HttpResponse.json({ detail: { error: "stale_version" } }, { status: 409 }));
    await userEvent.type(await screen.findByLabelText("Opis zdjęcia"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Zdjęcie zmieniło się w innej karcie");
    expect(within(alert).getByRole("button", { name: "Wczytaj ponownie" })).toBeInTheDocument();
  });

  it("needs a domain first and a label on every mark", async () => {
    mountEditor(photo({ domain_id: null, version: 0, caption: null, regions: [] }), () => HttpResponse.json(photo()));
    expect(await screen.findByText(/Najpierw wybierz dziedzinę/)).toBeInTheDocument();
    await userEvent.selectOptions(await screen.findByLabelText("Dziedzina"), "d1");
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 300, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(await screen.findByText(/1 bez etykiety/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
    await userEvent.type(screen.getByRole("combobox", { name: "Etykieta oznaczenia 1" }), "matka");
    await userEvent.click(await screen.findByRole("option", { name: /Matka pszczela/ }));
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeEnabled();
  });

  it("is read-only in an archived domain", async () => {
    mountEditor(photo(), () => HttpResponse.json(photo()), [{ ...BEES, status: "ARCHIVED" }]);
    expect(await screen.findByText(/zdjęcie można tylko oglądać/)).toBeInTheDocument();
    expect(screen.getByLabelText("Opis zdjęcia")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Usuń oznaczenie/ })).not.toBeInTheDocument();
  });
});

describe("PhotosPanel", () => {
  it("lists only image attachments, linking to the editor", () => {
    const base = { note_id: "n1", size: 1, scan_status: "CLEAN", keep_location: false, created_at: "2026-10-21T10:00:00Z" } as const;
    const attachments = [
      { ...base, id: "a1", filename: "ul.jpg", mime_type: "image/jpeg" },
      { ...base, id: "a2", filename: "raport.pdf", mime_type: "application/pdf" },
    ] as Attachment[];
    render(<PhotosPanel noteId="n1" attachments={attachments} />);
    const link = screen.getByRole("link", { name: "Oznacz: ul.jpg" });
    expect(link).toHaveAttribute("href", "/notebook/n1/photos/a1");
    expect(screen.getByRole("img", { name: "ul.jpg" })).toHaveAttribute("src", "/api/notes/n1/attachments/a1/image");
    expect(screen.queryByText(/raport.pdf/)).not.toBeInTheDocument();
  });
});
