"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { useToast } from "@/components/ui/Toast";
import { RateLimited } from "@/lib/api";
import { isModelMissing, MODEL_MISSING, useFeature } from "@/lib/capabilities";
import { useCreateNote, useNotes, usePromote, usePromotion } from "@/lib/notebook";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium", timeStyle: "short" });

export function NotebookList() {
  const notes = useNotes();
  const create = useCreateNote();
  const router = useRouter();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promotionId, setPromotionId] = useState<string | null>(null);

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">Mój notatnik</h1>
          <p className="text-sm text-text-muted">Prywatny i zaszyfrowany. Nikt poza Tobą nie ma do niego dostępu.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" disabled={selected.size === 0} onClick={() => setPromoteOpen(true)}>
            Utwórz wniosek do Wiki ({selected.size})
          </Button>
          <Button
            busy={create.isPending}
            onClick={async () => {
              const note = await create.mutateAsync("Nowa notatka");
              router.push(`/notebook/${note.id}`);
            }}
          >
            Nowa notatka
          </Button>
        </div>
      </div>

      {promotionId && <PromotionStatus promotionId={promotionId} onDismiss={() => setPromotionId(null)} />}

      {notes.isPending && <p className="text-text-muted">Ładowanie…</p>}
      {notes.isError && <p role="alert" className="text-danger">Nie udało się wczytać notatek.</p>}
      {notes.data?.length === 0 && <p className="text-text-muted">Nie masz jeszcze notatek.</p>}
      {notes.data && notes.data.length > 0 && (
        <ul className="flex flex-col gap-2">
          {notes.data.map((note) => (
            <li key={note.id}>
              <Card className="flex items-center gap-3 p-4">
                <input
                  type="checkbox"
                  checked={selected.has(note.id)}
                  onChange={() => toggle(note.id)}
                  aria-label={`Zaznacz: ${note.title}`}
                />
                <Link href={`/notebook/${note.id}`} className="flex-1 font-medium hover:text-primary-strong">
                  {note.title}
                </Link>
                <time dateTime={note.updated_at} className="text-xs text-text-muted">
                  {DATE.format(new Date(note.updated_at))}
                </time>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <PromoteDialog
        open={promoteOpen}
        noteIds={[...selected]}
        onClose={() => setPromoteOpen(false)}
        onPromoted={(id) => {
          setPromoteOpen(false);
          setSelected(new Set());
          setPromotionId(id);
        }}
      />
    </div>
  );
}

function PromoteDialog({
  open,
  noteIds,
  onClose,
  onPromoted,
}: {
  open: boolean;
  noteIds: string[];
  onClose: () => void;
  onPromoted: (promotionId: string) => void;
}) {
  const [titleHint, setTitleHint] = useState("");
  const promote = usePromote();
  const available = useFeature("promotion");
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const result = await promote.mutateAsync({ noteIds, titleHint });
      toast("Wniosek przyjęty. Przygotowujemy szkic artykułu.", "ok");
      onPromoted(result.promotion_id);
    } catch (error) {
      toast(
        error instanceof RateLimited
          ? "Dzienny limit wniosków został wyczerpany."
          : isModelMissing(error)
            ? `${MODEL_MISSING}: wnioski do Wiki są wyłączone.`
            : "Nie udało się utworzyć wniosku.",
        "error",
      );
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Wniosek do Wiki">
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
        <p>
          Z zaznaczonych notatek ({noteIds.length}) model dopuszczony do danych prywatnych przygotuje szkic artykułu. Dane
          osobowe (imiona, PESEL, adresy, numery działek…) zostaną usunięte, a szkic trafi do Rady Ekspertów.
        </p>
        {!available && <ModelMissing what="Tworzenie wniosków do Wiki" />}
        <label className="flex flex-col gap-1">
          Proponowany tytuł (opcjonalnie)
          <input
            value={titleHint}
            maxLength={200}
            onChange={(event) => setTitleHint(event.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button type="submit" busy={promote.isPending} disabled={!available}>
            Utwórz wniosek
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

const PROMOTION_STATUS: Record<string, string> = {
  PENDING: "Wniosek w kolejce…",
  RUNNING: "Przygotowywanie szkicu…",
  DONE: "Szkic gotowy",
  FAILED: "Nie udało się przygotować szkicu",
};

function PromotionStatus({ promotionId, onDismiss }: { promotionId: string; onDismiss: () => void }) {
  const promotion = usePromotion(promotionId);
  const data = promotion.data;
  return (
    <Card role="status" className="flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm">
        <p className="font-medium">{data ? PROMOTION_STATUS[data.status] : "Sprawdzanie wniosku…"}</p>
        {data?.status === "DONE" && (
          <p className="text-text-muted">
            Usunięte dane osobowe: {Object.values(data.pii_stats as Record<string, number>).reduce((a, b) => a + b, 0)}.{" "}
            <Link href={`/wiki/${data.article_id}`} className="text-primary-strong underline">
              Zobacz szkic
            </Link>
          </p>
        )}
        {data?.status === "FAILED" && <p className="text-danger">Spróbuj ponownie później.</p>}
      </div>
      <Button variant="ghost" onClick={onDismiss}>
        Zamknij
      </Button>
    </Card>
  );
}
