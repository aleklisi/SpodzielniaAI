"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useToast } from "@/components/ui/Toast";
import { eta, llmError, useCancelGeneration, useIndex, useRebuildIndex, type IndexGeneration } from "@/lib/llm-admin";

const STATUS: Record<IndexGeneration["status"], { label: string; tone: "primary" | "warn" | "neutral" | "danger" }> = {
  SERVING: { label: "Używana", tone: "primary" },
  BUILDING: { label: "W budowie", tone: "warn" },
  RETIRED: { label: "Wycofana", tone: "neutral" },
  CANCELLED: { label: "Anulowana", tone: "danger" },
};

function duration(seconds: number): string {
  if (seconds < 90) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? `${minutes} min` : `${Math.round(minutes / 60)} h`;
}

function Generations({ title, rows, onCancel, busy }: { title: string; rows: IndexGeneration[]; onCancel: (n: number) => void; busy: boolean }) {
  const shown = rows.filter((g) => !g.dropped || g.status === "SERVING");
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-medium">{title}</h3>
      {shown.length === 0 && <p className="text-sm text-text-muted">Brak wersji: indeks powstanie po skonfigurowaniu modelu.</p>}
      <ul className="flex flex-col gap-2">
        {shown.map((g) => {
          const left = g.status === "BUILDING" ? eta(g) : null;
          const percent = g.total > 0 ? Math.min(100, Math.round((g.done / g.total) * 100)) : 100;
          return (
            <li key={g.number} className="rounded-lg border border-border p-3 text-sm" aria-label={`Wersja ${g.number}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">Wersja {g.number}</span>
                <Badge tone={STATUS[g.status].tone}>{STATUS[g.status].label}</Badge>
                <span className="text-text-muted">
                  {g.model} · wymiar {g.dimension}
                </span>
                {g.status === "BUILDING" && (
                  <Button variant="secondary" className="ml-auto" busy={busy} onClick={() => onCancel(g.number)}>
                    Anuluj budowę
                  </Button>
                )}
              </div>
              {g.status === "BUILDING" && (
                <div className="mt-2 flex flex-col gap-1">
                  <progress className="w-full" max={100} value={percent} aria-label={`Postęp wersji ${g.number}`} />
                  <span className="text-xs text-text-muted">
                    {g.done} / {g.total} ({percent}%){g.failed > 0 && ` · błędy: ${g.failed}`}
                    {left !== null && ` · pozostało ok. ${duration(left)}`}
                  </span>
                </div>
              )}
              {g.status === "RETIRED" && g.drop_after && !g.dropped && (
                <p className="mt-1 text-xs text-text-muted">Usunięta po {new Date(g.drop_after).toLocaleString("pl-PL")}.</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Index versions and the background re-embedding after a model change (10.3, 10.6). */
export function IndexPanel() {
  const index = useIndex();
  const rebuild = useRebuildIndex();
  const cancel = useCancelGeneration();
  const toast = useToast();
  if (index.isPending) return <p className="text-text-muted">Ładowanie…</p>;
  if (!index.data) return <Card role="alert">Nie udało się wczytać indeksów.</Card>;
  const data = index.data;

  async function run(work: () => Promise<unknown>, done: string) {
    try {
      await work();
      toast(done, "ok");
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-3xl text-sm text-text-muted">
        Zmiana modelu embeddingów buduje w tle nową wersję indeksu. Do jej ukończenia wyszukiwanie korzysta ze starej wersji i
        jej własnego modelu; po ukończeniu wersje zamieniają się, a stara jest usuwana po okresie przejściowym. Modele w
        chmurze są ograniczone do {data.reembed_per_minute} elementów na minutę.
      </p>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-lg font-semibold">Indeks tekstu</h2>
          <span className="text-sm text-text-muted">Zmiana modelu przetworzy ponownie {data.items} elementów.</span>
          <Button
            variant="secondary"
            className="ml-auto"
            busy={rebuild.isPending}
            onClick={() => void run(() => rebuild.mutateAsync(), "Rozpoczęto budowę nowej wersji indeksu.")}
          >
            Przebuduj indeks
          </Button>
        </div>
        <Generations
          title="Wersje"
          rows={data.generations}
          busy={cancel.isPending}
          onCancel={(n) => void run(() => cancel.mutateAsync(n), "Anulowano budowę; obecna wersja działa dalej.")}
        />
      </Card>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-lg font-semibold">Indeks atlasu</h2>
          <span className="text-sm text-text-muted">Zmiana modelu przetworzy ponownie {data.atlas_items} zdjęć.</span>
        </div>
        <Generations
          title="Wersje"
          rows={data.atlas_generations}
          busy={cancel.isPending}
          onCancel={(n) => void run(() => cancel.mutateAsync(n), "Anulowano budowę; obecna wersja działa dalej.")}
        />
      </Card>
    </div>
  );
}
