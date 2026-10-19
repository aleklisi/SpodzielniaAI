"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { ssrfError } from "@/lib/messages";
import { useAddLink } from "@/lib/notebook";

export function LinkDialog({ noteId, open, onClose }: { noteId: string; open: boolean; onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const addLink = useAddLink(noteId);
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await addLink.mutateAsync(url.trim());
      if (!result.ok) {
        setError(ssrfError(result.reason));
        return;
      }
      toast("Strona zostanie pobrana i dodana do notatki.", "ok");
      setUrl("");
      onClose();
    } catch {
      setError("Nie udało się dodać adresu.");
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Dodaj stronę internetową">
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Adres strony
          <input
            type="url"
            required
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://"
            className="rounded-lg border border-border bg-surface px-3 py-2"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button type="submit" busy={addLink.isPending}>
            Dodaj
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
