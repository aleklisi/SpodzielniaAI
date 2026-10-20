"use client";

import { useState } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { llmError, PURPOSE_ORDER, PURPOSES, REVISION_STATUS, useRollback, type Revision } from "@/lib/llm-admin";

const TONE = { DRAFT: "neutral", ACTIVE: "primary", ARCHIVED: "neutral" } as const;

export function HistoryPanel({ revisions }: { revisions: Revision[] }) {
  const [restoring, setRestoring] = useState<Revision | null>(null);
  const [comment, setComment] = useState("");
  const rollback = useRollback();
  const toast = useToast();

  async function confirm() {
    if (!restoring) return;
    try {
      const restored = await rollback.mutateAsync({ number: restoring.number, comment: comment.trim() || undefined });
      toast(`Przywrócono konfigurację jako rewizję ${restored.number}.`, "ok");
      setRestoring(null);
      setComment("");
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ol className="flex flex-col gap-3" aria-label="Rewizje">
        {revisions.map((r) => (
          <li key={r.number} className="rounded-xl border border-border bg-surface p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-display text-base font-semibold">Rewizja {r.number}</span>
                <Badge tone={TONE[r.status]}>{REVISION_STATUS[r.status]}</Badge>
                {r.rolled_back_from && <Badge tone="warn">przywrócona z {r.rolled_back_from}</Badge>}
              </div>
              {r.status === "ARCHIVED" && (
                <Button variant="secondary" onClick={() => setRestoring(r)}>
                  Przywróć
                </Button>
              )}
            </div>
            <p className="mt-1 text-text-muted">
              {r.comment ?? "bez komentarza"} · utworzona {new Date(r.created_at).toLocaleString("pl-PL")}
              {r.activated_at && ` · aktywowana ${new Date(r.activated_at).toLocaleString("pl-PL")}`}
            </p>
            {r.routes && (
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">
                {PURPOSE_ORDER.filter((p) => r.routes?.[p]).map((p) => (
                  <li key={p} className="flex flex-wrap items-center gap-1">
                    {PURPOSES[p].label}: {r.routes?.[p]?.name} {r.routes?.[p] && <KindBadge kind={r.routes[p].kind} />}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      <Dialog open={restoring !== null} onClose={() => setRestoring(null)} title={`Przywrócić rewizję ${restoring?.number ?? ""}?`}>
        <div className="flex flex-col gap-3 text-sm">
          <p>
            Konfiguracja z tej rewizji zostanie aktywowana jako nowa rewizja (historia się nie zmienia). Obowiązują bieżące
            zasady serwera, więc np. modele chmurowe przy wyłączonej chmurze zostaną odrzucone.
          </p>
          <label className="flex flex-col gap-1">
            Komentarz (opcjonalny)
            <input
              className="rounded-lg border border-border bg-surface px-3 py-2"
              value={comment}
              maxLength={500}
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRestoring(null)}>
              Anuluj
            </Button>
            <Button busy={rollback.isPending} onClick={() => void confirm()}>
              Przywróć
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
