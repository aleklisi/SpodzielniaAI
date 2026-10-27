"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { TextDiff } from "@/components/review/TextDiff";
import { DECISIONS, validateDecision, type DecisionError } from "@/components/review/validate";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { articleText, reviewErrorKey, ReviewRefused, useArticle, useArticles, useDecide, type Article, type Decision } from "@/lib/wiki";

export function DecisionForm({ article, isOwn }: { article: Article; isOwn: boolean }) {
  const t = useTranslations("review");
  const wiki = useTranslations("wiki.reviewErrors");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [comment, setComment] = useState("");
  const [supersedesId, setSupersedesId] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const messages = (keys: DecisionError[]) => keys.map((key) => t(`validation.${key}`));
  const decide = useDecide();
  const toast = useToast();
  const canonical = useArticles();
  const superseded = useArticle(supersedesId);

  if (isOwn) {
    return (
      <p role="note" className="rounded-lg border border-border p-3 text-sm text-text-muted">
        {t("decision.coAuthor")}
      </p>
    );
  }
  if (decide.error instanceof StepUpRequired) return <StepUpNotice stepUp={decide.error.stepUp} />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const found = validateDecision({ decision, comment, supersedesId }, article.id);
    setErrors(messages(found));
    if (found.length || !decision) return;
    try {
      const result = await decide.mutateAsync({ articleId: article.id, decision, version: article.version, comment, supersedesId });
      toast(
        result.status === "CANONICAL"
          ? t("decision.canonical")
          : decision === "APPROVE"
            ? t("decision.approved", { approvals: result.approvals, quorum: result.quorum })
            : t("decision.saved"),
        "ok",
      );
    } catch (error) {
      if (error instanceof ReviewRefused) {
        const key = reviewErrorKey(error.code);
        setErrors([key ? wiki(key) : t("decision.failed")]);
      } else if (!(error instanceof StepUpRequired)) setErrors([t("decision.failed")]);
    }
  }

  const candidates = (canonical.data ?? []).filter((a) => a.id !== article.id);
  return (
    <form onSubmit={submit} className="flex flex-col gap-4" aria-label={t("decision.form")}>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">{t("decision.legend")}</legend>
        {DECISIONS.map((d) => (
          <label key={d.value} className="flex items-center gap-2 text-sm">
            <input type="radio" name="decision" value={d.value} checked={decision === d.value} onChange={() => setDecision(d.value)} />
            {t(`decision.${d.value}`)}
          </label>
        ))}
      </fieldset>
      {decision === "APPROVE" && (
        <label className="flex flex-col gap-1 text-sm">
          {t("decision.supersedes")}
          <select
            value={supersedesId ?? ""}
            onChange={(event) => setSupersedesId(event.target.value || null)}
            className="rounded-lg border border-border bg-surface px-3 py-2"
          >
            <option value="">{t("decision.supersedesNone")}</option>
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
        {t("decision.comment")}
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
          {t("decision.submit")}
        </Button>
      </div>
    </form>
  );
}
