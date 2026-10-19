"use client";

import Link from "next/link";

import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { StatusPill } from "@/components/ui/StatusPill";
import type { Article } from "@/lib/wiki";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "long" });

function name(display: string | null | undefined): string {
  return display ?? "Członek spółdzielni";
}

/**
 * One article: status, verification, authors and approving experts, then its blocks, each with
 * its author in the gutter (block-level provenance, ARCHITECTURE §3). Withheld blocks show
 * why. There is deliberately no export or "copy all": the server-side shield is the control,
 * this only avoids inviting bulk copying.
 */
export function ArticleView({ article }: { article: Article }) {
  const authors = new Map(article.authors.map((a) => [a.id, name(a.display_name)]));
  return (
    <article className="flex flex-col gap-5">
      {article.status === "SUPERSEDED" && article.superseded_by_id && (
        <div role="note" className="rounded-lg border border-warn bg-warn/10 p-3 text-sm">
          Ten artykuł został zastąpiony nowszą wersją.{" "}
          <Link href={`/wiki/${article.superseded_by_id}`} className="font-medium underline">
            Przejdź do aktualnego artykułu
          </Link>
        </div>
      )}
      {article.review_alert && (
        <div role="note" className="rounded-lg border border-danger bg-danger/10 p-3 text-sm">
          {article.review_alert}
        </div>
      )}
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={article.status} />
          <span className="text-xs text-text-muted">wersja {article.version}</span>
        </div>
        <h1 className="font-display text-3xl font-semibold">{article.title}</h1>
        <dl className="grid gap-x-6 gap-y-1 text-sm text-text-muted sm:grid-cols-[auto_1fr]">
          <dt>Ostatnia weryfikacja</dt>
          <dd>{article.last_verified_at ? DATE.format(new Date(article.last_verified_at)) : "jeszcze nie zweryfikowano"}</dd>
          <dt>Autorzy</dt>
          <dd>{article.authors.map((a) => name(a.display_name)).join(", ") || "–"}</dd>
          <dt>Zatwierdzili</dt>
          <dd>{article.approving_experts.map((e) => name(e.display_name)).join(", ") || "–"}</dd>
        </dl>
      </header>
      <div className="flex flex-col gap-4">
        {article.blocks.map((block) => (
          <section key={block.id} className="grid gap-3 sm:grid-cols-[10rem_1fr]" aria-label={`Akapit ${block.position + 1}`}>
            <aside className="text-xs text-text-muted sm:border-r sm:border-border sm:pr-3 sm:text-right">
              {authors.get(block.author_id) ?? name(null)}
            </aside>
            {block.withheld ? (
              <div className="flex flex-col gap-1">
                <p className="text-sm italic text-text-muted">{block.notice}</p>
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
