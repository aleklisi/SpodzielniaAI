"use client";

import Link from "next/link";

import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { useArticles } from "@/lib/wiki";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium" });

export function WikiList() {
  const articles = useArticles();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Baza wiedzy</h1>
        <p className="text-sm text-text-muted">Artykuły zatwierdzone przez Radę Ekspertów spółdzielni.</p>
      </div>
      {articles.isPending && <p className="text-text-muted">Ładowanie…</p>}
      {articles.isError && <p role="alert" className="text-danger">Nie udało się wczytać artykułów.</p>}
      {articles.data?.length === 0 && <p className="text-text-muted">Brak zatwierdzonych artykułów.</p>}
      <ul className="grid gap-3 sm:grid-cols-2">
        {articles.data?.map((a) => (
          <li key={a.id}>
            <Link href={`/wiki/${a.id}`} className="block h-full">
              <Card className="flex h-full flex-col gap-2 transition-colors hover:border-primary">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="font-display font-semibold">{a.title}</h2>
                  <StatusPill status={a.status} />
                </div>
                <p className="text-xs text-text-muted">
                  {a.authors.map((x) => x.display_name ?? "Członek spółdzielni").join(", ")}
                  {a.last_verified_at && ` · zweryfikowano ${DATE.format(new Date(a.last_verified_at))}`}
                </p>
              </Card>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
