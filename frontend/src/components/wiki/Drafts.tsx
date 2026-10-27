"use client";

import { FilePen } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";
import { useContributions } from "@/lib/wiki";

const OPEN = new Set(["DRAFT", "REVISION_REQUIRED", "IN_REVIEW"]);

export function Drafts() {
  const t = useTranslations("wiki.drafts");
  const contributions = useContributions();
  const drafts = (contributions.data ?? []).filter((c) => OPEN.has(c.status));
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("title")} description={t("description")} />
      {contributions.isPending && <Loading />}
      {contributions.data && drafts.length === 0 && (
        <EmptyState icon={FilePen} title={t("emptyTitle")}>
          {t("emptyBody")}
        </EmptyState>
      )}
      {drafts.length > 0 && (
        <DataTable label={t("table")}>
          <THead>
            <Tr>
              <Th>{t("article")}</Th>
              <Th>{t("status")}</Th>
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
                <Td label={t("status")}>
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
