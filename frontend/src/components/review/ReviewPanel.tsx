"use client";

import { useState } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { DecisionForm } from "@/components/review/DecisionForm";
import { ArticleView } from "@/components/wiki/ArticleView";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { StatusPill } from "@/components/ui/StatusPill";
import { StepUpRequired } from "@/lib/api";
import { useArticle, useReviewQueue, type QueueItem } from "@/lib/wiki";

/** Articles the system sent back (a co-author withdrew consent, 4.3) come first. */
export function orderQueue(items: QueueItem[]): QueueItem[] {
  return [...items].sort(
    (a, b) =>
      Number(!!b.review_alert) - Number(!!a.review_alert) ||
      a.status_changed_at.localeCompare(b.status_changed_at),
  );
}

export function ReviewPanel() {
  const queue = useReviewQueue(true);
  const [selected, setSelected] = useState<string | null>(null);
  const article = useArticle(selected);

  if (queue.error instanceof StepUpRequired) return <StepUpNotice stepUp={queue.error.stepUp} />;
  const items = orderQueue(queue.data ?? []);
  const current = items.find((i) => i.article_id === selected);
  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <section aria-labelledby="queue-title" className="flex flex-col gap-3">
        <h1 id="queue-title" className="font-display text-2xl font-semibold">
          Recenzje
        </h1>
        {queue.isPending && <p className="text-text-muted">Ładowanie kolejki…</p>}
        {queue.isError && !(queue.error instanceof StepUpRequired) && <p role="alert" className="text-danger">Nie udało się wczytać kolejki.</p>}
        {queue.data?.length === 0 && <p className="text-text-muted">Kolejka jest pusta.</p>}
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={item.article_id}>
              <button
                type="button"
                onClick={() => setSelected(item.article_id)}
                aria-current={item.article_id === selected}
                className={cn(
                  "w-full rounded-xl border bg-surface p-3 text-left",
                  item.article_id === selected ? "border-primary" : "border-border",
                  item.review_alert && "border-l-4 border-l-danger",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{item.title}</span>
                  <StatusPill status={item.status} />
                </div>
                {item.review_alert && <p className="mt-1 text-xs text-danger">{item.review_alert}</p>}
                <p className="mt-1 text-xs text-text-muted">
                  Zatwierdzenia: {item.approvals}/{item.quorum}
                  {item.is_own && " · Twój artykuł"}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Recenzowany artykuł">
        {!selected && <Card className="text-text-muted">Wybierz artykuł z kolejki.</Card>}
        {selected && article.isPending && <p className="text-text-muted">Ładowanie…</p>}
        {article.data && current && (
          <div className="flex flex-col gap-6">
            <ArticleView article={article.data} />
            <Card>
              <DecisionForm key={`${article.data.id}:${article.data.version}`} article={article.data} isOwn={current.is_own} />
            </Card>
          </div>
        )}
      </section>
    </div>
  );
}
