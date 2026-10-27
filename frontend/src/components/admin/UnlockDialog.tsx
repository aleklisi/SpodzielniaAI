"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { useUnlock, type UserCoverage } from "@/lib/admin";

/** Lifts the shield's lock (6.6). Used by the security dashboard and the user console (8.5). */
export type UnlockTarget = Pick<UserCoverage, "user_id" | "display_name" | "email" | "locked_reason">;

export function UnlockDialog({ user, onClose }: { user: UnlockTarget | null; onClose: () => void }) {
  const t = useTranslations("adminSecurity.unlock");
  const common = useTranslations("common");
  const [reason, setReason] = useState("");
  const [resetRisk, setResetRisk] = useState(true);
  const unlock = useUnlock();
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!user || reason.trim().length < 10) return;
    try {
      await unlock.mutateAsync({ userId: user.user_id, reason: reason.trim(), resetRisk });
      toast(t("done"), "ok");
      setReason("");
      onClose();
    } catch {
      toast(t("failed"), "error");
    }
  }

  return (
    <Dialog open={user !== null} onClose={onClose} title={t("title")}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
        <p>
          {user?.display_name ?? user?.email ?? user?.user_id} · {t("reason")} <code>{user?.locked_reason ?? "–"}</code>
        </p>
        <label className="flex flex-col gap-1">
          {t("justification")}
          <textarea
            rows={3}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2"
          />
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={resetRisk} onChange={(event) => setResetRisk(event.target.checked)} />
          {t("resetRisk")}
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" disabled={reason.trim().length < 10} busy={unlock.isPending}>
            {t("submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
