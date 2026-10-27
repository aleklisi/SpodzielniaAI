"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { atlasImageUrl } from "@/lib/atlas-submissions";
import { useFeature } from "@/lib/capabilities";
import { isRunning, recognitionErrorKey, useRecognitions, useRecognize, type RecognitionErrorKey, type Suggestion } from "@/lib/recognition";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm";

/**
 * "Recognise": what the verified atlas says is on this photo. Suggestions are drawn dashed on
 * the photo until the member accepts (they become marks) or dismisses them.
 */
export function RecognitionPanel({
  noteId,
  attachmentId,
  domainId,
  dismissed,
  onAccept,
  onDismiss,
}: {
  noteId: string;
  attachmentId: string;
  domainId: string | null;
  dismissed: Set<string>;
  onAccept: (s: Suggestion) => void;
  onDismiss: (s: Suggestion) => void;
}) {
  const t = useTranslations("notebook.recognition");
  const recognitions = useRecognitions(noteId, attachmentId);
  const recognize = useRecognize(noteId, attachmentId);
  const available = useFeature("recognition");
  const [question, setQuestion] = useState("");
  const [allDomains, setAllDomains] = useState(false);
  const [error, setError] = useState<RecognitionErrorKey | null>(null);
  const latest = recognitions.data?.[0];
  const running = isRunning(latest) || recognize.isPending;

  async function start() {
    setError(null);
    try {
      await recognize.mutateAsync({ question: question.trim() || null, domain_id: allDomains ? null : domainId, all_domains: allDomains });
    } catch (e) {
      setError(recognitionErrorKey(e));
    }
  }

  const open = (latest?.suggestions ?? []).filter((s) => !dismissed.has(suggestionKey(s)));

  return (
    <Card className="flex flex-col gap-3 text-sm" aria-labelledby="recognize-title">
      <h2 id="recognize-title" className="font-display text-lg font-semibold">
        {t("title")}
      </h2>
      <p className="text-text-muted">{t("intro")}</p>
      {!available && <ModelMissing what={t("feature")} />}
      <label className="flex flex-col gap-1">
        {t("question")}
        <input className={INPUT} maxLength={300} placeholder={t("placeholder")} value={question} onChange={(e) => setQuestion(e.target.value)} />
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={allDomains} onChange={(e) => setAllDomains(e.target.checked)} />
        {t("allDomains")}
      </label>
      <Button onClick={() => void start()} busy={running} disabled={!available || (!domainId && !allDomains)} className="self-start">
        {t("submit")}
      </Button>
      {running && (
        <p role="status" className="text-text-muted">
          {t("running")}
        </p>
      )}
      {error && (
        <p role="alert" className="text-danger">
          {t(`errors.${error}`)}
        </p>
      )}
      {latest?.status === "FAILED" && <p role="alert" className="text-danger">{t("failed")}</p>}
      {latest?.status === "DONE" && (
        <div className="flex flex-col gap-2" aria-label={t("result")} role="region">
          <p className="whitespace-pre-wrap">{latest.answer}</p>
          {latest.vlm === "unavailable" && <p className="text-xs text-warn">{t("vlmUnavailable")}</p>}
          {open.length > 0 && (
            <ul className="flex flex-col gap-1" aria-label={t("suggestions")}>
              {open.map((s) => (
                <li key={suggestionKey(s)} className="flex items-center justify-between gap-2 rounded-lg border border-dashed border-primary p-2">
                  <span>
                    {s.label_name} <span className="text-xs text-text-muted">({Math.round(s.confidence * 100)}%)</span>
                  </span>
                  <span className="flex gap-1">
                    <Button size="sm" onClick={() => onAccept(s)}>
                      {t("accept")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => onDismiss(s)}>
                      {t("dismiss")}
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {latest.citations.length > 0 && (
            <div>
              <p className="text-xs text-text-muted">{t("basedOn")}</p>
              <ul className="mt-1 flex flex-wrap gap-2" aria-label={t("sources")}>
                {latest.citations.map((c) => (
                  <li key={c.image_id}>
                    <Link href={`/atlas/${c.image_id}`} className="flex flex-col items-center text-xs" title={c.labels.join(", ")}>
                      {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes */}
                      <img src={atlasImageUrl(c.image_id, "thumb")} alt={c.labels.join(", ")} className="h-14 w-20 rounded object-cover" />
                      <span className="text-text-muted">{c.author_name ?? t("member")}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function suggestionKey(s: Suggestion): string {
  return `${s.label_id}:${s.x}:${s.y}:${s.w}:${s.h}`;
}
