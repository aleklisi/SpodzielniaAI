"use client";

import { Images } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { useDomains, useLabels } from "@/lib/atlas";
import { PAGE_SIZE, useGallery, type GalleryFilters } from "@/lib/atlas-gallery";
import { atlasImageUrl } from "@/lib/atlas-submissions";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm @4xl:min-w-48";
const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium" });

/** „Atlas”: photos verified by the experts, by domain and label. */
export function AtlasGallery() {
  const domains = useDomains();
  const [filters, setFilters] = useState<GalleryFilters>({ domainId: "", labelId: "", q: "", offset: 0 });
  const labels = useLabels(filters.domainId || null, "");
  const page = useGallery(filters);
  const total = page.data?.total ?? 0;
  const domainName = new Map((domains.data ?? []).map((d) => [d.id, d.name]));

  function set(patch: Partial<GalleryFilters>) {
    setFilters((f) => ({ ...f, offset: 0, ...patch }));
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Atlas"
        description="Zdjęcia z oznaczonymi elementami, sprawdzone przez ekspertów spółdzielni. Swoje zdjęcia dodajesz z notatnika („Oznacz”, potem „Przekaż do atlasu”)."
      />
      {/* One filter per row on a phone, side by side when there is room (12.5). */}
      <div className="grid gap-3 text-sm @xl:grid-cols-3 @4xl:flex @4xl:flex-wrap" role="search">
        <label className="flex flex-col gap-1">
          Dziedzina
          <select className={INPUT} value={filters.domainId} onChange={(e) => set({ domainId: e.target.value, labelId: "" })}>
            <option value="">Wszystkie</option>
            {domains.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Etykieta
          <select className={INPUT} value={filters.labelId} disabled={!filters.domainId} onChange={(e) => set({ labelId: e.target.value })}>
            <option value="">Wszystkie</option>
            {labels.data
              ?.filter((l) => l.status === "ACTIVE")
              .map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Szukaj
          <input className={INPUT} type="search" placeholder="np. matka, matecznik" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
        </label>
      </div>
      {page.isError && (
        <p role="alert" className="text-danger">
          Nie udało się wczytać atlasu.
        </p>
      )}
      {page.data?.items.length === 0 && <EmptyState icon={Images} title="Brak zdjęć dla tych filtrów." />}
      <ul className="grid grid-cols-2 gap-3 @2xl:grid-cols-3 @4xl:grid-cols-4 @6xl:grid-cols-5 @md:gap-4" aria-label="Zdjęcia atlasu">
        {page.data?.items.map((photo) => (
          <li key={photo.id}>
            <Link
              href={`/atlas/${photo.id}`}
              className="group flex h-full flex-col overflow-hidden rounded-xl border border-border bg-surface transition-colors hover:border-primary"
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes */}
              <img src={atlasImageUrl(photo.id, "thumb")} alt={photo.caption ?? photo.labels.join(", ")} loading="lazy" className="aspect-[4/3] w-full object-cover" />
              <div className="flex flex-1 flex-col gap-1 p-3 text-sm">
                <span className="font-medium break-words group-hover:underline">{photo.labels.join(", ")}</span>
                <span className="text-xs text-text-muted">
                  {domainName.get(photo.domain_id) ?? ""} · {photo.author_name ?? "Członek spółdzielni"}
                  {photo.verified_at && ` · ${DATE.format(new Date(photo.verified_at))}`}
                </span>
              </div>
            </Link>
          </li>
        ))}
      </ul>
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between gap-2 text-sm">
          <Button variant="secondary" disabled={filters.offset === 0} onClick={() => setFilters((f) => ({ ...f, offset: Math.max(0, f.offset - PAGE_SIZE) }))}>
            Poprzednie
          </Button>
          <span className="text-text-muted">
            {filters.offset + 1}–{Math.min(filters.offset + PAGE_SIZE, total)} z {total}
          </span>
          <Button variant="secondary" disabled={filters.offset + PAGE_SIZE >= total} onClick={() => setFilters((f) => ({ ...f, offset: f.offset + PAGE_SIZE }))}>
            Następne
          </Button>
        </div>
      )}
    </div>
  );
}
