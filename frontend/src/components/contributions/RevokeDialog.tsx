"use client";

import { useTranslations } from "next-intl";
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
  const t = useTranslations("contributions.revokeDialog");
  const common = useTranslations("common");
  const actions = useTranslations("contributions");
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
    <Dialog open={open} onClose={onClose} title={t("title")}>
      {consent.error instanceof StepUpRequired ? (
        <StepUpNotice
          stepUp={consent.error.stepUp}
          reason={t("stepUp")}
        />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
          <p>{t.rich("scope", { scope: scopeLabel, strong: (chunks) => <strong>{chunks}</strong> })}</p>
          <ul className="list-disc pl-5">
            <li>{t("erased")}</li>
            {coAuthored && <li>{t("coAuthored")}</li>}
            <li>{t("suspendInstead")}</li>
          </ul>
          <label className="flex flex-col gap-1">
            <span>{t.rich("confirm", { word: CONFIRM_WORD, code: (chunks) => <code className="font-mono">{chunks}</code> })}</span>
            <input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              className="rounded-lg border border-border bg-surface px-3 py-2 font-mono"
            />
          </label>
          {consent.isError && !(consent.error instanceof StepUpRequired) && (
            <p role="alert" className="text-danger">
              {t("failed")}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              {common("cancel")}
            </Button>
            <Button type="submit" variant="danger" disabled={typed !== CONFIRM_WORD} busy={consent.isPending}>
              {actions("revoke")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
