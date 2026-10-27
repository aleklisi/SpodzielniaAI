"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { isModelMissing, useFeature } from "@/lib/capabilities";
import { useAskNotebook } from "@/lib/notebook";

/** "Ask your notebook": private RAG over the member's own notes (5.1), models cleared for
 * private data only (10.1); off without one (10.2). */
export function AskPanel() {
  const t = useTranslations("notebook.ask");
  const [question, setQuestion] = useState("");
  const ask = useAskNotebook();
  const available = useFeature("notebook_answers");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim().length >= 3) ask.mutate(question.trim());
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="font-display text-lg font-semibold">{t("title")}</h2>
      {!available && <ModelMissing what={t("feature")} />}
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label className="sr-only" htmlFor="ask-notebook">
          {t("label")}
        </label>
        <textarea
          id="ask-notebook"
          rows={3}
          maxLength={1000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t("placeholder")}
          className="rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        />
        <Button type="submit" busy={ask.isPending} disabled={!available || question.trim().length < 3}>
          {t("submit")}
        </Button>
      </form>
      {ask.isError && (
        <p role="alert" className="text-sm text-danger">
          {isModelMissing(ask.error) ? t("modelMissing") : t("failed")}
        </p>
      )}
      {ask.data && (
        <div className="flex flex-col gap-2 text-sm" aria-live="polite">
          {ask.data.no_answer ? (
            <p className="italic text-text-muted">{ask.data.answer_markdown}</p>
          ) : (
            <SafeMarkdown markdown={ask.data.answer_markdown} citations />
          )}
          {ask.data.citations.length > 0 && (
            <ol className="list-decimal pl-5">
              {ask.data.citations.map((c, i) => (
                <li key={c.note_id} id={`cite-${i + 1}`}>
                  <Link href={`/notebook/${c.note_id}`} className="text-primary-strong underline">
                    {c.note_title}
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  );
}
