"use client";

import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { TextDiff } from "@/components/review/TextDiff";
import { DECISIONS, validateDecision } from "@/components/review/validate";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { articleText, REVIEW_ERRORS, ReviewRefused, useArticle, useArticles, useDecide, type Article, type Decision } from "@/lib/wiki";

export function DecisionForm({ article, isOwn }: { article: Article; isOwn: boolean }) {
  const [decision, setDecision] = useState<Decision | null>(null);
  const [comment, setComment] = useState("");
  const [supersedesId, setSupersedesId] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const decide = useDecide();
  const toast = useToast();
  const canonical = useArticles();
  const superseded = useArticle(supersedesId);

  if (isOwn) {
    return (
      <p role="note" className="rounded-lg border border-border p-3 text-sm text-text-muted">
        Jesteś współautorem tego artykułu, więc nie możesz go recenzować.
      </p>
    );
  }
  if (decide.error instanceof StepUpRequired) return <StepUpNotice stepUp={decide.error.stepUp} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateDecision({ decision, comment, supersedesId }, article.id);
    setErrors(found);
    if (found.length || !decision) return;
    try {
      const result = await decide.mutateAsync({ articleId: article.id, decision, version: article.version, comment, supersedesId });
      toast(
        result.status === "CANONICAL"
          ? "Artykuł jest kanoniczny i trafił do bazy wiedzy."
          : decision === "APPROVE"
            ? `Zatwierdzono (${result.approvals}/${result.quorum}).`
            : "Decyzja zapisana.",
        "ok",
      );
    } catch (error) {
      if (error instanceof ReviewRefused) setErrors([REVIEW_ERRORS[error.code] ?? "Nie udało się zapisać decyzji."]);
      else if (!(error instanceof StepUpRequired)) setErrors(["Nie udało się zapisać decyzji."]);
    }
  }

  const candidates = (canonical.data ?? []).filter((a) => a.id !== article.id);
  return (
    <form onSubmit={submit} className="flex flex-col gap-4" aria-label="Decyzja recenzenta">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">Decyzja</legend>
        {DECISIONS.map((d) => (
          <label key={d.value} className="flex items-center gap-2 text-sm">
            <input type="radio" name="decision" value={d.value} checked={decision === d.value} onChange={() => setDecision(d.value)} />
            {d.label}
          </label>
        ))}
      </fieldset>
      {decision === "APPROVE" && (
        <label className="flex flex-col gap-1 text-sm">
          Zastępuje artykuł (opcjonalnie)
          <select
            value={supersedesId ?? ""}
            onChange={(event) => setSupersedesId(event.target.value || null)}
            className="rounded-lg border border-border bg-surface px-3 py-2"
          >
            <option value="">– nie zastępuje innego –</option>
            {candidates.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {supersedesId && superseded.data && <TextDiff before={articleText(superseded.data)} after={articleText(article)} />}
      <label className="flex flex-col gap-1 text-sm">
        Komentarz dla autorów
        <textarea
          rows={4}
          value={comment}
          maxLength={5000}
          onChange={(event) => setComment(event.target.value)}
          className="rounded-lg border border-border bg-surface px-3 py-2"
        />
      </label>
      {errors.length > 0 && (
        <ul role="alert" className="list-disc pl-5 text-sm text-danger">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div>
        <Button type="submit" busy={decide.isPending}>
          Zapisz decyzję
        </Button>
      </div>
    </form>
  );
}
