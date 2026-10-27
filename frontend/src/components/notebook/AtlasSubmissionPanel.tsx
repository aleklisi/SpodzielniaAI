"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ImageAnnotator } from "@/components/annotator/ImageAnnotator";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import {
  ATLAS_STATUS_TONE,
  atlasImageUrl,
  privacyFlag,
  submissionErrorKey,
  type SubmissionErrorKey,
  usePhotoSubmissions,
  useRequestSubmission,
  useSendSubmission,
  useWithdrawSubmission,
  type Submission,
} from "@/lib/atlas-submissions";
import type { Mark } from "@/lib/photos";

/**
 * "Submit to the atlas": prepare → review exactly what would be shared → send or withdraw.
 * Disabled while the editor has unsaved changes (the server prepares the saved version).
 */
export function AtlasSubmissionPanel({
  noteId,
  attachmentId,
  canSubmit,
  reason,
}: {
  noteId: string;
  attachmentId: string;
  canSubmit: boolean;
  reason: string | null;
}) {
  const t = useTranslations("notebook.submission");
  const status = useTranslations("common.atlasStatus");
  const alerts = useTranslations("common.alerts");
  const submissions = usePhotoSubmissions(attachmentId);
  const request = useRequestSubmission(noteId, attachmentId);
  const [error, setError] = useState<SubmissionErrorKey | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const latest = submissions.data?.[0];
  const atlas = latest?.status === "SENT" ? latest.atlas : null;
  const live = atlas && (atlas.status === "IN_REVIEW" || atlas.status === "APPROVED");
  const busy = latest?.status === "PENDING";

  async function start() {
    setError(null);
    try {
      await request.mutateAsync();
    } catch (e) {
      setError(submissionErrorKey(e));
    }
  }

  const review = submissions.data?.find((s) => s.id === reviewing) ?? null;

  return (
    <Card className="flex flex-col gap-3" aria-labelledby="atlas-title">
      <h2 id="atlas-title" className="font-display text-lg font-semibold">
        {t("title")}
      </h2>
      {atlas && (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={ATLAS_STATUS_TONE[atlas.status]}>{status(atlas.status)}</Badge>
          {atlas.review_alert && (
            <span className="text-warn">{atlas.review_alert === "revision_required" ? alerts("revision_required") : atlas.review_alert}</span>
          )}
        </p>
      )}
      {busy && (
        <p role="status" className="text-sm text-text-muted">
          {t("preparing")}
        </p>
      )}
      {latest?.status === "FAILED" && (
        <p role="alert" className="text-sm text-danger">
          {t(`errors.${submissionErrorKey(latest.error ?? "", "prepareFailed")}`)}
        </p>
      )}
      {latest?.status === "PREPARED" && (
        <div className="flex flex-col gap-2 text-sm">
          <p>{t("prepared")}</p>
          <Button onClick={() => setReviewing(latest.id)}>{t("review")}</Button>
        </div>
      )}
      {!busy && latest?.status !== "PREPARED" && !live && (
        <div className="flex flex-col gap-2 text-sm">
          <p className="text-text-muted">{t("intro")}</p>
          <Button onClick={() => void start()} busy={request.isPending} disabled={!canSubmit}>
            {atlas?.status === "REVISION_REQUIRED" ? t("resend") : t("send")}
          </Button>
          {!canSubmit && reason && <p className="text-xs text-text-muted">{reason}</p>}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {t(`errors.${error}`)}
        </p>
      )}
      {review && <ReviewDialog key={review.id} submission={review} attachmentId={attachmentId} onClose={() => setReviewing(null)} />}
    </Card>
  );
}

function ReviewDialog({ submission, attachmentId, onClose }: { submission: Submission; attachmentId: string; onClose: () => void }) {
  const t = useTranslations("notebook.submission");
  const draft = submission.atlas!;
  const send = useSendSubmission(attachmentId);
  const withdraw = useWithdrawSubmission(attachmentId);
  const toast = useToast();
  const [showAuthor, setShowAuthor] = useState<boolean | null>(null); // no default: the author decides
  const [allowExport, setAllowExport] = useState(false);
  const [confirmPeople, setConfirmPeople] = useState(false);
  const [error, setError] = useState<SubmissionErrorKey | null>(null);
  const marks: Mark[] = draft.regions.map((r) => ({ key: r.id, label: r.label, x: r.x, y: r.y, w: r.w, h: r.h, description: r.description ?? "", source: "USER" }));
  const ready = showAuthor !== null && (!draft.needs_people_confirmation || confirmPeople);

  async function doSend() {
    if (showAuthor === null) return;
    setError(null);
    try {
      await send.mutateAsync({ id: submission.id, show_author: showAuthor, allow_export: allowExport, confirm_no_people: confirmPeople });
      toast(t("sent"), "ok");
      onClose();
    } catch (e) {
      setError(submissionErrorKey(e));
    }
  }

  async function doWithdraw() {
    try {
      await withdraw.mutateAsync(submission.id);
      onClose();
    } catch (e) {
      setError(submissionErrorKey(e));
    }
  }

  return (
    <Dialog open onClose={onClose} title={t("dialogTitle")}>
      <div className="flex max-h-[80vh] flex-col gap-4 overflow-y-auto text-sm">
        <ImageAnnotator
          src={atlasImageUrl(draft.id, "preview")}
          alt={draft.caption ?? t("defaultAlt")}
          aspect={draft.width / draft.height}
          marks={marks}
          selected={null}
          onSelect={() => undefined}
          onChange={() => undefined}
          readOnly
        />
        <section>
          <h3 className="font-medium">{t("caption")}</h3>
          <p className="whitespace-pre-wrap">{draft.caption ?? "—"}</p>
          {submission.pii_replaced > 0 && (
            <p className="mt-1 text-xs text-warn">{t("piiReplaced", { count: submission.pii_replaced })}</p>
          )}
        </section>
        <section>
          <h3 className="font-medium">{t("marks")}</h3>
          <ol className="list-decimal pl-5">
            {draft.regions.map((r) => (
              <li key={r.id}>
                {r.label.name}
                {r.label.status === "PROPOSED" && <span className="text-xs text-warn">{t("proposedLabel")}</span>}
                {r.description && <span className="text-text-muted">: {r.description}</span>}
              </li>
            ))}
          </ol>
        </section>
        <p className="text-xs text-text-muted">{t("noMetadata")}</p>
        {draft.privacy_check === "unavailable" && (
          <p className="text-warn">{t("checkUnavailable")}</p>
        )}
        {draft.privacy_flags.length > 0 && (
          <div role="alert" className="rounded-lg border border-warn p-3">
            <p className="font-medium text-warn">
              {t("mayShow", {
                flags: draft.privacy_flags
                  .map((f) => {
                    const flag = privacyFlag(f);
                    return flag ? t(`privacy.${flag}`) : f;
                  })
                  .join(", "),
              })}
            </p>
            {draft.needs_people_confirmation && (
              <label className="mt-2 flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={confirmPeople} onChange={(e) => setConfirmPeople(e.target.checked)} />
                {t("confirmPeople")}
              </label>
            )}
          </div>
        )}
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 font-medium">{t("byline")}</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name="show-author" checked={showAuthor === true} onChange={() => setShowAuthor(true)} />
            {t("showName")}
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" name="show-author" checked={showAuthor === false} onChange={() => setShowAuthor(false)} />
            {t("anonymous")}
          </label>
        </fieldset>
        <label className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={allowExport} onChange={(e) => setAllowExport(e.target.checked)} />
          {t("allowExport")}
        </label>
        {error && (
          <p role="alert" className="text-danger">
            {t(`errors.${error}`)}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => void doWithdraw()} busy={withdraw.isPending}>
            {t("withdraw")}
          </Button>
          <Button onClick={() => void doSend()} busy={send.isPending} disabled={!ready}>
            {t("toExperts")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
