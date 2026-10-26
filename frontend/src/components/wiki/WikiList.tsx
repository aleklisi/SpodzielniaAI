"use client";

import { BookOpen } from "lucide-react";
import Link from "next/link";

import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";
import { useArticles } from "@/lib/wiki";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium" });

export function WikiList() {
  const articles = useArticles();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Baza wiedzy" description="Artykuły zatwierdzone przez Radę Ekspertów spółdzielni." />
      {articles.isPending && <Loading />}
      {articles.isError && <p role="alert" className="text-danger">Nie udało się wczytać artykułów.</p>}
      {articles.data?.length === 0 && <EmptyState icon={BookOpen} title="Brak zatwierdzonych artykułów." />}
      <ul className="grid gap-3 @2xl:grid-cols-2 @5xl:grid-cols-3">
        {articles.data?.map((a) => (
          <li key={a.id}>
            <Link href={`/wiki/${a.id}`} className="block h-full">
              <Card className="flex h-full flex-col gap-2 transition-colors hover:border-primary">
                <div className="flex items-start justify-between gap-2">
                  <h2 className="font-display font-semibold break-words">{a.title}</h2>
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
