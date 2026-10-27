"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { DecisionForm } from "@/components/review/DecisionForm";
import { ArticleView } from "@/components/wiki/ArticleView";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { PageHeader } from "@/components/ui/PageHeader";
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
  const t = useTranslations("review");
  const alerts = useTranslations("common.alerts");
  const queue = useReviewQueue(true);
  // `/review?article=<id>` opens that article directly (links from notifications, E2E).
  const params = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("article"));
  const article = useArticle(selected);
  const detail = useRef<HTMLElement>(null);

  if (queue.error instanceof StepUpRequired) return <StepUpNotice stepUp={queue.error.stepUp} />;
  const items = orderQueue(queue.data ?? []);
  const current = items.find((i) => i.article_id === selected);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <span className="flex gap-4 text-sm font-medium">
            <Link href="/review/atlas" className="text-primary-strong">
              {t("atlas")}
            </Link>
            <Link href="/review/labels" className="text-primary-strong">
              {t("labels")}
            </Link>
          </span>
        }
      />
      {/* Queue and article side by side when there is room; one at a time on a phone (12.6). */}
      <div className="grid gap-6 @4xl:grid-cols-[20rem_minmax(0,1fr)]">
        <section aria-label={t("queue")} className={cn("flex flex-col gap-3", selected && "@max-4xl:hidden")}>
          {queue.isPending && <p className="text-text-muted">{t("loadingQueue")}</p>}
          {queue.isError && !(queue.error instanceof StepUpRequired) && <p role="alert" className="text-danger">{t("queueFailed")}</p>}
          {queue.data?.length === 0 && <p className="text-text-muted">{t("queueEmpty")}</p>}
          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <li key={item.article_id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelected(item.article_id);
                    detail.current?.scrollIntoView?.({ block: "start" });
                  }}
                  aria-current={item.article_id === selected}
                  className={cn(
                    "w-full rounded-xl border bg-surface p-3 text-left",
                    item.article_id === selected ? "border-primary" : "border-border",
                    item.review_alert && "border-l-4 border-l-danger",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium break-words">{item.title}</span>
                    <StatusPill status={item.status} />
                  </div>
                  {item.review_alert && (
                    <p className="mt-1 text-xs text-danger">
                      {item.review_alert === "revision_required" ? alerts("revision_required") : item.review_alert}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-text-muted">
                    {t("approvals", { approvals: item.approvals, quorum: item.quorum })}
                    {item.is_own && t("own")}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section ref={detail} aria-label={t("reviewed")} className={cn("flex scroll-mt-20 flex-col gap-4", !selected && "@max-4xl:hidden")}>
          {selected && (
            <Button variant="ghost" className="self-start @4xl:hidden" onClick={() => setSelected(null)}>
              {t("backToQueue")}
            </Button>
          )}
          {!selected && <Card className="text-text-muted">{t("choose")}</Card>}
          {selected && article.isPending && <p className="text-text-muted">{t("loading")}</p>}
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
    </div>
  );
}
