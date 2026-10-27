"use client";

import { HandHeart } from "lucide-react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { RevokeDialog } from "@/components/contributions/RevokeDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { describeResult, useConsent, useHistory, type ConsentAction } from "@/lib/contributions";
import { useContributions, type Contribution } from "@/lib/wiki";

function counts(c: Contribution) {
  const by = { ACTIVE: 0, SUSPENDED: 0, REVOKED: 0 };
  for (const b of c.blocks) by[b.consent_status] += 1;
  return by;
}

export function ContributionsPanel() {
  const t = useTranslations("contributions");
  const contributions = useContributions();
  const consent = useConsent();
  const toast = useToast();
  const [revoke, setRevoke] = useState<{ ids: string[] | null; label: string; coAuthored: boolean } | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  async function act(action: Exclude<ConsentAction, "revoke">, ids: string[] | null, key: string) {
    setPending(key);
    try {
      const result = await consent.mutateAsync({ action, articleIds: ids });
      toast(describeResult(t, action, result), "ok");
    } catch {
      toast(t("failed"), "error");
    } finally {
      setPending(null);
    }
  }

  const rows = contributions.data ?? [];
  const live = rows.filter((c) => counts(c).ACTIVE + counts(c).SUSPENDED > 0);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      {live.length > 0 && (
        <div className="flex flex-wrap gap-2" aria-label={t("all")}>
          <Button variant="secondary" busy={pending === "all:suspend"} onClick={() => act("suspend", null, "all:suspend")}>
            {t("suspendAll")}
          </Button>
          <Button variant="secondary" busy={pending === "all:resume"} onClick={() => act("resume", null, "all:resume")}>
            {t("resumeAll")}
          </Button>
          <Button
            variant="danger"
            onClick={() => setRevoke({ ids: null, label: t("revokeAllScope"), coAuthored: rows.some((c) => c.co_authored) })}
          >
            {t("revokeAll")}
          </Button>
        </div>
      )}
      {contributions.isPending && <Loading />}
      {contributions.data?.length === 0 && <EmptyState icon={HandHeart} title={t("empty")} />}
      {rows.length > 0 && (
        <DataTable label={t("table")}>
          <THead>
            <Tr>
              <Th>{t("article")}</Th>
              <Th>{t("status")}</Th>
              <Th>{t("yourBlocks")}</Th>
              <Th>{t("actions")}</Th>
            </Tr>
          </THead>
          <TBody>
            {rows.map((c) => {
              const n = counts(c);
              const key = c.article_id;
              return (
                <Tr key={key}>
                  <Td primary className="md:py-3">
                    <Link href={`/wiki/${c.article_id}`} className="font-medium hover:text-primary-strong">
                      {c.title}
                    </Link>
                    {c.co_authored && (
                      <Badge tone="accent" className="ml-2">
                        {t("coAuthored")}
                      </Badge>
                    )}
                  </Td>
                  <Td label={t("status")} className="md:py-3">
                    <StatusPill status={c.status} />
                  </Td>
                  <Td label={t("yourBlocks")} className="md:py-3">
                    {n.ACTIVE > 0 && <div>{t("active", { count: n.ACTIVE })}</div>}
                    {n.SUSPENDED > 0 && <div className="text-warn">{t("suspended", { count: n.SUSPENDED })}</div>}
                    {n.REVOKED > 0 && <div className="text-text-muted">{t("revoked", { count: n.REVOKED })}</div>}
                  </Td>
                  <Td className="md:py-3">
                    <div className="flex flex-wrap gap-2 max-md:mt-1">
                      {n.ACTIVE > 0 && (
                        <Button variant="secondary" busy={pending === `${key}:suspend`} onClick={() => act("suspend", [key], `${key}:suspend`)}>
                          {t("suspend")}
                        </Button>
                      )}
                      {n.SUSPENDED > 0 && (
                        <Button variant="secondary" busy={pending === `${key}:resume`} onClick={() => act("resume", [key], `${key}:resume`)}>
                          {t("resume")}
                        </Button>
                      )}
                      {n.ACTIVE + n.SUSPENDED > 0 && (
                        <Button
                          variant="ghost"
                          className="text-danger"
                          onClick={() =>
                            setRevoke({
                              ids: [key],
                              label: t("revokeScope", { count: n.ACTIVE + n.SUSPENDED, title: c.title }),
                              coAuthored: c.co_authored,
                            })
                          }
                        >
                          {t("revoke")}
                        </Button>
                      )}
                    </div>
                  </Td>
                </Tr>
              );
            })}
          </TBody>
        </DataTable>
      )}
      <History />
      <RevokeDialog
        open={revoke !== null}
        onClose={() => setRevoke(null)}
        articleIds={revoke?.ids ?? null}
        scopeLabel={revoke?.label ?? ""}
        coAuthored={revoke?.coAuthored ?? false}
        onDone={(result) => {
          setRevoke(null);
          toast(describeResult(t, "revoke", result), "ok");
        }}
      />
    </div>
  );
}

function History() {
  const t = useTranslations("contributions.history");
  const format = useFormatter();
  const history = useHistory();
  if (!history.data?.length) return null;
  return (
    <Card>
      <h2 className="mb-3 font-display text-lg font-semibold">{t("title")}</h2>
      <ol className="flex flex-col gap-2 text-sm">
        {history.data.map((e, i) => (
          <li key={i} className="flex flex-wrap justify-between gap-2 border-b border-border pb-2 last:border-0">
            <span>
              {t(e.event)}
              {e.blocks != null && e.event !== "ARTICLE_DEGRADED" && t("blocks", { count: e.blocks })}
              {e.scope === "all" && t("allArticles")}
              {e.article_id && (
                <>
                  {" · "}
                  <Link href={`/wiki/${e.article_id}`} className="underline">
                    {t("article")}
                  </Link>
                </>
              )}
            </span>
            <time dateTime={e.occurred_at} className="text-text-muted">
              {format.dateTime(new Date(e.occurred_at), "dateTime")}
            </time>
          </li>
        ))}
      </ol>
    </Card>
  );
}
