"use client";

import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { StepUpRequired } from "@/lib/api";
import type { ConsentResult } from "@/lib/contributions";
import { useConsent } from "@/lib/contributions";

export const CONFIRM_WORD = "REVOKE";

/**
 * Irreversible revoke: the text of the blocks is deleted and their vectors hard-deleted.
 * The member types REVOKE; a login older than the server's limit gets a step-up link.
 * The dialog waits for the server's answer (no optimistic update).
 */
export function RevokeDialog({
  open,
  onClose,
  articleIds,
  scopeLabel,
  coAuthored,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  articleIds: string[] | null;
  scopeLabel: string;
  coAuthored: boolean;
  onDone: (result: ConsentResult) => void;
}) {
  const [typed, setTyped] = useState("");
  const consent = useConsent();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (typed !== CONFIRM_WORD) return;
    try {
      const result = await consent.mutateAsync({ action: "revoke", articleIds });
      setTyped("");
      onDone(result);
    } catch {
      // Shown below (step-up or a generic error).
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Usuń trwale swój wkład">
      {consent.error instanceof StepUpRequired ? (
        <StepUpNotice
          stepUp={consent.error.stepUp}
          reason="Trwałe usunięcie wymaga niedawnego logowania. Zaloguj się ponownie i powtórz operację."
        />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
          <p>
            Usuwasz: <strong>{scopeLabel}</strong>.
          </p>
          <ul className="list-disc pl-5">
            <li>Treść Twoich bloków zostanie skasowana, a ich wektory usunięte z wyszukiwarki. Tego nie da się cofnąć.</li>
            {coAuthored && (
              <li>
                Artykuły współtworzone z innymi autorami stracą status kanoniczny i wrócą do Rady Ekspertów z informacją o
                wycofaniu części treści.
              </li>
            )}
            <li>Jeśli chcesz tylko czasowo ukryć swój wkład, użyj „Wstrzymaj”.</li>
          </ul>
          <label className="flex flex-col gap-1">
            Aby potwierdzić, wpisz <code className="font-mono">{CONFIRM_WORD}</code>
            <input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              className="rounded-lg border border-border bg-surface px-3 py-2 font-mono"
            />
          </label>
          {consent.isError && !(consent.error instanceof StepUpRequired) && (
            <p role="alert" className="text-danger">
              Nie udało się usunąć. Spróbuj ponownie.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Anuluj
            </Button>
            <Button type="submit" variant="danger" disabled={typed !== CONFIRM_WORD} busy={consent.isPending}>
              Usuń trwale
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
