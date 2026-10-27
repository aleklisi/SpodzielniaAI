"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { llmErrorKey, PURPOSE_ORDER, useRollback, type Revision } from "@/lib/llm-admin";

const TONE = { DRAFT: "neutral", ACTIVE: "primary", ARCHIVED: "neutral" } as const;

export function HistoryPanel({ revisions }: { revisions: Revision[] }) {
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const format = useFormatter();
  const when = (value: string) => format.dateTime(new Date(value), "dateTime");
  const [restoring, setRestoring] = useState<Revision | null>(null);
  const [comment, setComment] = useState("");
  const rollback = useRollback();
  const toast = useToast();

  async function confirm() {
    if (!restoring) return;
    try {
      const restored = await rollback.mutateAsync({ number: restoring.number, comment: comment.trim() || undefined });
      toast(t("history.restored", { number: restored.number }), "ok");
      setRestoring(null);
      setComment("");
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <ol className="flex flex-col gap-3" aria-label={t("history.revisions")}>
        {revisions.map((r) => (
          <li key={r.number} className="rounded-xl border border-border bg-surface p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-display text-base font-semibold">{t("history.revisionN", { number: r.number })}</span>
                <Badge tone={TONE[r.status]}>{t(`revisionStatus.${r.status}`)}</Badge>
                {r.rolled_back_from && <Badge tone="warn">{t("history.restoredFrom", { number: r.rolled_back_from })}</Badge>}
              </div>
              {r.status === "ARCHIVED" && (
                <Button variant="secondary" onClick={() => setRestoring(r)}>
                  {t("history.restore")}
                </Button>
              )}
            </div>
            <p className="mt-1 text-text-muted">
              {r.comment ?? t("history.noComment")}
              {t("history.created", { date: when(r.created_at) })}
              {r.activated_at && t("history.activated", { date: when(r.activated_at) })}
            </p>
            {r.routes && (
              <ul className="mt-2 grid gap-1 sm:grid-cols-2">
                {PURPOSE_ORDER.filter((p) => r.routes?.[p]).map((p) => (
                  <li key={p} className="flex flex-wrap items-center gap-1">
                    {t(`purposes.${p}.label`)}: {r.routes?.[p]?.name} {r.routes?.[p] && <KindBadge kind={r.routes[p].kind} />}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      <Dialog open={restoring !== null} onClose={() => setRestoring(null)} title={t("history.confirmTitle", { number: restoring?.number ?? "" })}>
        <div className="flex flex-col gap-3 text-sm">
          <p>{t("history.body")}</p>
          <label className="flex flex-col gap-1">
            {t("history.comment")}
            <input
              className="rounded-lg border border-border bg-surface px-3 py-2"
              value={comment}
              maxLength={500}
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRestoring(null)}>
              {common("cancel")}
            </Button>
            <Button busy={rollback.isPending} onClick={() => void confirm()}>
              {t("history.restore")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
