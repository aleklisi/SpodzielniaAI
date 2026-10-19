"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type DragEvent } from "react";

import { cn } from "@/components/ui/cn";
import { keys } from "@/lib/notebook";
import { UploadFailed, uploadAttachment, type Attachment } from "@/lib/upload";

type Phase =
  | { kind: "uploading"; progress: number }
  | { kind: "scanning" }
  | { kind: "stored"; attachment: Attachment }
  | { kind: "rejected"; message: string; status: number };

interface Item {
  id: number;
  name: string;
  phase: Phase;
}

export type Uploader = typeof uploadAttachment;

export function UploadZone({ noteId, upload = uploadAttachment }: { noteId: string; upload?: Uploader }) {
  const client = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [keepLocation, setKeepLocation] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const nextId = useRef(0);

  const update = (id: number, phase: Phase) =>
    setItems((current) => current.map((item) => (item.id === id ? { ...item, phase } : item)));

  async function send(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const id = nextId.current++;
      setItems((current) => [...current, { id, name: file.name, phase: { kind: "uploading", progress: 0 } }]);
      try {
        const attachment = await upload(noteId, file, {
          keepLocation,
          // All bytes sent: the server is now scanning the file (ClamAV) before it answers.
          onProgress: (fraction) => update(id, fraction < 1 ? { kind: "uploading", progress: fraction } : { kind: "scanning" }),
        });
        update(id, { kind: "stored", attachment });
        void client.invalidateQueries({ queryKey: keys.note(noteId) });
      } catch (error) {
        const failed = error instanceof UploadFailed ? error : new UploadFailed(0, undefined);
        update(id, { kind: "rejected", message: failed.message, status: failed.status });
      }
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) void send(event.dataTransfer.files);
  }

  return (
    <section aria-labelledby={`${hintId}-title`} className="flex flex-col gap-3">
      <h2 id={`${hintId}-title`} className="font-display text-lg font-semibold">
        Załączniki
      </h2>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-6 text-center text-sm",
          dragging ? "border-primary bg-primary/5" : "border-border",
        )}
      >
        <p>Przeciągnij tu zdjęcia, nagrania lub dokumenty</p>
        <button type="button" className="text-primary-strong underline" onClick={() => input.current?.click()}>
          albo wybierz pliki
        </button>
        <input
          ref={input}
          type="file"
          multiple
          className="sr-only"
          aria-label="Wybierz pliki do wgrania"
          onChange={(event) => {
            if (event.target.files?.length) void send(event.target.files);
            event.target.value = "";
          }}
        />
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={keepLocation}
          onChange={(event) => setKeepLocation(event.target.checked)}
          aria-describedby={hintId}
          className="mt-1"
        />
        <span>
          Zachowaj lokalizację GPS
          <span id={hintId} className="block text-text-muted">
            Zdjęcia z telefonu często zawierają współrzędne, np. położenie pasieki. Domyślnie usuwamy je przed
            zapisaniem pliku.
          </span>
        </span>
      </label>
      {items.length > 0 && (
        <ul className="flex flex-col gap-2" aria-label="Wgrywane pliki">
          {items.map((item) => (
            <li key={item.id} className="rounded-lg border border-border p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{item.name}</span>
                <UploadStatus phase={item.phase} />
              </div>
              {item.phase.kind === "uploading" && (
                <progress className="mt-2 w-full" max={1} value={item.phase.progress} aria-label={`Postęp: ${item.name}`} />
              )}
              {item.phase.kind === "rejected" && (
                <p role="alert" className="mt-1 text-danger">
                  {item.phase.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function UploadStatus({ phase }: { phase: Phase }) {
  switch (phase.kind) {
    case "uploading":
      return <span className="text-text-muted">Wysyłanie {Math.round(phase.progress * 100)}%</span>;
    case "scanning":
      return <span className="text-text-muted">Skanowanie antywirusowe…</span>;
    case "stored":
      return <span className="text-ok">Zapisano, przetwarzanie w toku</span>;
    case "rejected":
      return <span className="text-danger">Odrzucono</span>;
  }
}
