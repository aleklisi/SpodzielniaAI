"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ImageAnnotator, markColor, newMarkKey } from "@/components/annotator/ImageAnnotator";
import { AtlasSubmissionPanel } from "@/components/notebook/AtlasSubmissionPanel";
import { RecognitionPanel, suggestionKey } from "@/components/notebook/RecognitionPanel";
import { LabelCombobox } from "@/components/atlas/LabelCombobox";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { useToast } from "@/components/ui/Toast";
import { useDomains } from "@/lib/atlas";
import { useRecognitions, type Suggestion } from "@/lib/recognition";
import {
  imageUrl,
  MAX_MARKS,
  photoError,
  photoErrorCode,
  toInput,
  toMarks,
  useSavePhoto,
  usePhoto,
  type Mark,
  type Photo,
} from "@/lib/photos";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm";

/** Loads the photo, then hands it to the editor (keyed by version, so a reload resets it). */
export function PhotoEditor({ noteId, attachmentId }: { noteId: string; attachmentId: string }) {
  const photo = usePhoto(noteId, attachmentId);
  if (photo.isPending) return <p className="text-text-muted">Ładowanie zdjęcia…</p>;
  if (photo.isError || !photo.data)
    return (
      <p role="alert" className="text-danger">
        Nie znaleziono zdjęcia.
      </p>
    );
  return <Editor key={photo.data.version} noteId={noteId} attachmentId={attachmentId} photo={photo.data} onReload={() => void photo.refetch()} />;
}

function Editor({ noteId, attachmentId, photo, onReload }: { noteId: string; attachmentId: string; photo: Photo; onReload: () => void }) {
  const domains = useDomains();
  const save = useSavePhoto(noteId, attachmentId);
  const toast = useToast();
  const [domainId, setDomainId] = useState<string | null>(photo.domain_id);
  const [caption, setCaption] = useState(photo.caption ?? "");
  const [marks, setMarks] = useState<Mark[]>(() => toMarks(photo.regions));
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<{ message: string; stale: boolean } | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const recognitions = useRecognitions(noteId, attachmentId);
  const latestRecognition = recognitions.data?.[0];
  const ghosts =
    latestRecognition?.status === "DONE"
      ? latestRecognition.suggestions
          .filter((s) => !dismissed.has(suggestionKey(s)))
          .map((s) => ({ key: suggestionKey(s), label: s.label_name, x: s.x, y: s.y, w: s.w, h: s.h }))
      : [];

  function accept(s: Suggestion) {
    change([
      ...marks,
      { key: newMarkKey(), label: { id: s.label_id, name: s.label_name, status: "ACTIVE" }, x: s.x, y: s.y, w: s.w, h: s.h, description: "", source: "SUGGESTED" },
    ]);
    setDismissed((d) => new Set(d).add(suggestionKey(s)));
  }

  const domain = domains.data?.find((d) => d.id === domainId) ?? null;
  const archived = domain?.status === "ARCHIVED";
  const active = (domains.data ?? []).filter((d) => d.status === "ACTIVE" || d.id === photo.domain_id);
  const unlabeled = marks.filter((m) => !m.label).length;

  // Unsaved-changes guard for closing or reloading the tab.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function change(next: Mark[]) {
    setMarks(next.slice(0, MAX_MARKS));
    setDirty(true);
  }

  function edit(key: string, patch: Partial<Mark>) {
    change(marks.map((m) => (m.key === key ? { ...m, ...patch } : m)));
  }

  async function submit() {
    if (!domainId) return;
    setError(null);
    try {
      await save.mutateAsync(toInput(domainId, caption, marks, photo.version));
      setDirty(false);
      toast("Zapisano oznaczenia.", "ok");
    } catch (e) {
      setError({ message: photoError(e), stale: photoErrorCode(e) === "stale_version" });
    }
  }

  const alt = caption.trim() || "Zdjęcie z notatki";

  return (
    // Side by side once the workspace (not the window) is wide enough; details below on a phone (12.3).
    <div className="grid gap-6 @3xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section aria-label="Zdjęcie" className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <Link
            href={`/notebook/${noteId}`}
            className="text-sm text-text-muted hover:text-text"
            onClick={(e) => {
              if (dirty && !window.confirm("Masz niezapisane zmiany. Wyjść bez zapisywania?")) e.preventDefault();
            }}
          >
            ← Notatka
          </Link>
          <a href={imageUrl(noteId, attachmentId, "full")} target="_blank" rel="noopener" className="text-sm text-text-muted hover:text-text">
            Pełna rozdzielczość
          </a>
        </div>
        <ImageAnnotator
          src={imageUrl(noteId, attachmentId)}
          alt={alt}
          aspect={photo.width && photo.height ? photo.width / photo.height : null}
          marks={marks}
          selected={selected}
          onSelect={setSelected}
          onChange={change}
          readOnly={!domainId || archived}
          ghosts={ghosts}
        />
        {!domainId && <p className="text-sm text-warn">Najpierw wybierz dziedzinę zdjęcia: od niej zależą dostępne etykiety.</p>}
      </section>

      <aside className="flex flex-col gap-4" aria-label="Opis i oznaczenia">
        <Card className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Dziedzina</span>
            <select
              className={INPUT}
              value={domainId ?? ""}
              disabled={archived}
              onChange={(e) => {
                const next = e.target.value || null;
                if (next !== domainId && marks.length > 0 && !window.confirm("Zmiana dziedziny usunie etykiety oznaczeń. Kontynuować?")) return;
                setDomainId(next);
                setDirty(true);
                if (next !== domainId) change(marks.map((m) => ({ ...m, label: null })));
              }}
            >
              <option value="">Wybierz…</option>
              {active.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                  {d.status === "ARCHIVED" ? " (archiwum)" : ""}
                </option>
              ))}
            </select>
          </label>
          {archived && <p className="text-xs text-warn">Ta dziedzina jest zarchiwizowana: zdjęcie można tylko oglądać.</p>}
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Opis zdjęcia</span>
            <textarea
              className={INPUT}
              rows={3}
              maxLength={2000}
              value={caption}
              disabled={archived}
              onChange={(e) => {
                setCaption(e.target.value);
                setDirty(true);
              }}
            />
          </label>
        </Card>

        <section aria-labelledby="marks-title" className="flex flex-col gap-2">
          <h2 id="marks-title" className="font-display text-lg font-semibold">
            Oznaczenia ({marks.length})
          </h2>
          {marks.length === 0 && <p className="text-sm text-text-muted">Brak oznaczeń. Przeciągnij po zdjęciu, aby zaznaczyć element.</p>}
          <ol className="flex flex-col gap-2">
            {marks.map((mark, index) => (
              <li key={mark.key}>
                <Card
                  className={cn("flex flex-col gap-2 p-3", mark.key === selected && "border-primary")}
                  onClick={() => setSelected(mark.key)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      <span aria-hidden className={cn("inline-block h-3 w-3 rounded-sm", markColor(index).chip)} />
                      Oznaczenie {index + 1}
                      {mark.source === "SUGGESTED" && <span className="text-xs text-text-muted">(z rozpoznawania)</span>}
                    </span>
                    {!archived && (
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Usuń oznaczenie ${index + 1}`}
                        onClick={() => {
                          change(marks.filter((m) => m.key !== mark.key));
                          setSelected(null);
                        }}
                      >
                        Usuń
                      </Button>
                    )}
                  </div>
                  {domainId && (
                    <LabelCombobox
                      domainId={domainId}
                      label={`Etykieta oznaczenia ${index + 1}`}
                      value={mark.label}
                      disabled={archived}
                      onChange={(label) => edit(mark.key, { label: { id: label.id, name: label.name, status: label.status } })}
                    />
                  )}
                  <label className="flex flex-col gap-1 text-sm">
                    <span>Opis oznaczenia</span>
                    <textarea
                      className={INPUT}
                      rows={2}
                      maxLength={1000}
                      value={mark.description}
                      disabled={archived}
                      onChange={(e) => edit(mark.key, { description: e.target.value })}
                    />
                  </label>
                </Card>
              </li>
            ))}
          </ol>
        </section>

        {error && (
          <div role="alert" className="flex flex-col gap-2 text-sm text-danger">
            <p>{error.message}</p>
            {error.stale && (
              <Button variant="secondary" onClick={onReload}>
                Wczytaj ponownie
              </Button>
            )}
          </div>
        )}
        {unlabeled > 0 && <p className="text-sm text-warn">Nadaj etykietę każdemu oznaczeniu ({unlabeled} bez etykiety).</p>}
        <Button onClick={() => void submit()} busy={save.isPending} disabled={!domainId || archived || unlabeled > 0 || !dirty}>
          Zapisz
        </Button>
        <RecognitionPanel
          noteId={noteId}
          attachmentId={attachmentId}
          domainId={domainId}
          dismissed={dismissed}
          onAccept={accept}
          onDismiss={(s) => setDismissed((d) => new Set(d).add(suggestionKey(s)))}
        />
        <AtlasSubmissionPanel
          noteId={noteId}
          attachmentId={attachmentId}
          canSubmit={!dirty && photo.version > 0 && marks.length > 0 && !archived}
          reason={
            dirty
              ? "Najpierw zapisz zmiany."
              : photo.version === 0 || marks.length === 0
                ? "Zaznacz i opisz co najmniej jeden element, potem zapisz."
                : archived
                  ? "Dziedzina jest zarchiwizowana."
                  : null
          }
        />
      </aside>
    </div>
  );
}
