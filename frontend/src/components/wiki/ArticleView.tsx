"use client";

import { UserRound } from "lucide-react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";

import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { StatusPill } from "@/components/ui/StatusPill";
import type { Article } from "@/lib/wiki";


/**
 * One article: status, verification, authors and approving experts, then its blocks, each with
 * its author in the gutter (block-level provenance, ARCHITECTURE §3). Withheld blocks show
 * why. There is deliberately no export or "copy all": the server-side shield is the control,
 * this only avoids inviting bulk copying.
 */
export function ArticleView({ article }: { article: Article }) {
  const t = useTranslations("wiki");
  const alerts = useTranslations("common.alerts");
  const format = useFormatter();
  const name = (display: string | null | undefined) => display ?? t("member");
  const authors = new Map(article.authors.map((a) => [a.id, name(a.display_name)]));
  return (
    <article className="flex flex-col gap-5">
      {article.status === "SUPERSEDED" && article.superseded_by_id && (
        <div role="note" className="rounded-lg border border-warn bg-warn/10 p-3 text-sm">
          {t("article.superseded")}{" "}
          <Link href={`/wiki/${article.superseded_by_id}`} className="font-medium underline">
            {t("article.current")}
          </Link>
        </div>
      )}
      {article.review_alert && (
        <div role="note" className="rounded-lg border border-danger bg-danger/10 p-3 text-sm">
          {article.review_alert === "revision_required" ? alerts("revision_required") : article.review_alert}
        </div>
      )}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={article.status} />
          <span className="text-xs text-text-muted">{t("article.version", { version: article.version })}</span>
        </div>
        <h1 className="font-display text-2xl font-semibold break-words md:text-3xl">{article.title}</h1>
        <dl className="grid gap-x-6 gap-y-1 text-sm text-text-muted @lg:grid-cols-[auto_1fr]">
          <dt>{t("article.lastVerified")}</dt>
          <dd>{article.last_verified_at ? format.dateTime(new Date(article.last_verified_at), "long") : t("article.notVerified")}</dd>
          <dt>{t("article.authors")}</dt>
          <dd>{article.authors.map((a) => name(a.display_name)).join(", ") || "–"}</dd>
          <dt>{t("article.approvedBy")}</dt>
          <dd>{article.approving_experts.map((e) => name(e.display_name)).join(", ") || "–"}</dd>
        </dl>
      </header>
      <div className="flex flex-col gap-4">
        {article.blocks.map((block) => (
          // The author in a side margin when there is room, above the paragraph on a phone (12.4).
          <section key={block.id} className="grid gap-1 @2xl:grid-cols-[10rem_minmax(0,1fr)] @2xl:gap-3" aria-label={t("article.paragraph", { n: block.position + 1 })}>
            <aside className="flex items-center gap-1 text-xs text-text-muted @2xl:block @2xl:border-r @2xl:border-border @2xl:pr-3 @2xl:text-right">
              <UserRound aria-hidden className="size-3 @2xl:hidden" />
              {authors.get(block.author_id) ?? name(null)}
            </aside>
            {block.withheld ? (
              <div className="flex flex-col gap-1">
                <p className="text-sm italic text-text-muted">
                  {block.consent_status === "ACTIVE" ? block.notice : t(`article.withheld.${block.consent_status}`)}
                </p>
                {block.content && <SafeMarkdown markdown={block.content} />}
              </div>
            ) : (
              <SafeMarkdown markdown={block.content ?? ""} />
            )}
          </section>
        ))}
      </div>
    </article>
  );
}
