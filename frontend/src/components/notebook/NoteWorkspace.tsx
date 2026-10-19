"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AskPanel } from "@/components/notebook/AskPanel";
import { DerivativesPanel } from "@/components/notebook/DerivativesPanel";
import { LinkDialog } from "@/components/notebook/LinkDialog";
import { UploadZone } from "@/components/notebook/UploadZone";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/lib/api";
import { useAutosave, type SaveState } from "@/lib/autosave";
import { useDeleteNote, useNote, useSaveNote } from "@/lib/notebook";

const NoteEditor = dynamic(() => import("@/components/notebook/NoteEditor"), {
  ssr: false,
  loading: () => <div className="min-h-64 rounded-xl border border-border bg-surface p-4 text-text-muted">Ładowanie edytora…</div>,
});

const SAVE_LABEL: Record<SaveState, string> = {
  idle: "",
  pending: "Niezapisane zmiany",
  saving: "Zapisywanie…",
  saved: "Zapisano",
  error: "Nie udało się zapisać",
};

export function NoteWorkspace({ noteId }: { noteId: string }) {
  const note = useNote(noteId);
  const save = useSaveNote(noteId);
  const remove = useDeleteNote();
  const router = useRouter();
  const [linkOpen, setLinkOpen] = useState(false);
  const autosave = useAutosave((change: { title?: string; body?: string }) => save.mutateAsync(change));

  if (note.isPending) return <p className="text-text-muted">Ładowanie notatki…</p>;
  if (note.isError) {
    const missing = note.error instanceof ApiError && note.error.status === 404;
    return (
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold">{missing ? "Nie znaleziono notatki" : "Błąd"}</h1>
        <p className="text-text-muted">
          {missing ? "Ta notatka nie istnieje albo nie masz do niej dostępu." : "Nie udało się wczytać notatki."}
        </p>
        <Link href="/notebook" className="text-primary-strong underline">
          Wróć do notatnika
        </Link>
      </div>
    );
  }

  const data = note.data;
  return (
    <div className="grid gap-8 lg:grid-cols-[1fr_20rem]">
      <div className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link href="/notebook" className="text-sm text-text-muted hover:text-text">
            ← Notatnik
          </Link>
          <span role="status" className="text-sm text-text-muted">
            {SAVE_LABEL[autosave.state]}
          </span>
        </div>
        <label className="sr-only" htmlFor="note-title">
          Tytuł notatki
        </label>
        <input
          id="note-title"
          defaultValue={data.title}
          maxLength={200}
          onChange={(event) => {
            if (event.target.value.trim()) autosave.schedule({ title: event.target.value.trim() });
          }}
          className="bg-transparent font-display text-2xl font-semibold outline-none"
        />
        <NoteEditor markdown={data.body} onChange={(body) => autosave.schedule({ body })} />
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setLinkOpen(true)}>
            Dodaj stronę internetową
          </Button>
          <Button
            variant="danger"
            busy={remove.isPending}
            onClick={async () => {
              if (window.confirm("Usunąć notatkę wraz z załącznikami? Tej operacji nie można cofnąć.")) {
                await remove.mutateAsync(noteId);
                router.push("/notebook");
              }
            }}
          >
            Usuń notatkę
          </Button>
        </div>
        <UploadZone noteId={noteId} />
        <DerivativesPanel noteId={noteId} derivatives={data.derivatives} />
      </div>
      <aside className="flex flex-col gap-4">
        <AskPanel />
      </aside>
      <LinkDialog noteId={noteId} open={linkOpen} onClose={() => setLinkOpen(false)} />
    </div>
  );
}
