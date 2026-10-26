"use client";

import { FilePen } from "lucide-react";
import Link from "next/link";

import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";
import { useContributions } from "@/lib/wiki";

const OPEN = new Set(["DRAFT", "REVISION_REQUIRED", "IN_REVIEW"]);

export function Drafts() {
  const contributions = useContributions();
  const drafts = (contributions.data ?? []).filter((c) => OPEN.has(c.status));
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Moje szkice" description="Artykuły z Twoich wniosków przed zatwierdzeniem przez Radę Ekspertów." />
      {contributions.isPending && <Loading />}
      {contributions.data && drafts.length === 0 && (
        <EmptyState icon={FilePen} title="Nie masz otwartych szkiców.">
          Zaznacz notatki w notatniku i utwórz wniosek do Wiki, a szkic pojawi się tutaj.
        </EmptyState>
      )}
      {drafts.length > 0 && (
        <DataTable label="Szkice">
          <THead>
            <Tr>
              <Th>Artykuł</Th>
              <Th>Status</Th>
            </Tr>
          </THead>
          <TBody>
            {drafts.map((d) => (
              <Tr key={d.article_id}>
                <Td primary>
                  <Link href={`/wiki/${d.article_id}`} className="font-medium hover:text-primary-strong">
                    {d.title}
                  </Link>
                </Td>
                <Td label="Status">
                  <StatusPill status={d.status} />
                </Td>
              </Tr>
            ))}
          </TBody>
        </DataTable>
      )}
    </div>
  );
}
