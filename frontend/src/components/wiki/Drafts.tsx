"use client";

import Link from "next/link";

import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { useContributions } from "@/lib/wiki";

const OPEN = new Set(["DRAFT", "REVISION_REQUIRED", "IN_REVIEW"]);

export function Drafts() {
  const contributions = useContributions();
  const drafts = (contributions.data ?? []).filter((c) => OPEN.has(c.status));
  return (
    <div className="flex flex-col gap-4">
      <h1 className="font-display text-2xl font-semibold">Moje szkice</h1>
      <p className="text-sm text-text-muted">Artykuły z Twoich wniosków przed zatwierdzeniem przez Radę Ekspertów.</p>
      {contributions.isPending && <p className="text-text-muted">Ładowanie…</p>}
      {contributions.data && drafts.length === 0 && <p className="text-text-muted">Nie masz otwartych szkiców.</p>}
      <ul className="flex flex-col gap-2">
        {drafts.map((d) => (
          <li key={d.article_id}>
            <Link href={`/wiki/${d.article_id}`}>
              <Card className="flex items-center justify-between gap-2 p-4 hover:border-primary">
                <span className="font-medium">{d.title}</span>
                <StatusPill status={d.status} />
              </Card>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
