"use client";

import { useFormatter, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { useProcessors, type Processor } from "@/lib/processors";

function ProcessorCard({ p }: { p: Processor }) {
  const t = useTranslations("privacy");
  const format = useFormatter();
  return (
    <Card className="flex flex-col gap-2" aria-label={t("provider", { name: p.name })}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-display text-lg font-semibold">{p.name}</h2>
        <Badge tone={p.on_server ? "primary" : "neutral"}>{p.on_server ? t("onServer") : t("cloud")}</Badge>
        <span className="text-xs text-text-muted">{p.host}</span>
      </div>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm break-words">
        <dt className="text-text-muted">{t("region")}</dt>
        <dd>{p.region ?? "—"}</dd>
        <dt className="text-text-muted">{t("retention")}</dt>
        <dd>{p.retention ?? "—"}</dd>
        <dt className="text-text-muted">{t("contract")}</dt>
        <dd>{p.contract_ref ?? "—"}</dd>
        <dt className="text-text-muted">{t("clearedSince")}</dt>
        <dd>{p.cleared_since ? format.dateTime(new Date(p.cleared_since), "long") : "—"}</dd>
      </dl>
      {p.purposes.length > 0 ? (
        <ul className="list-disc pl-5 text-sm">
          {p.purposes.map((purpose) => (
            <li key={purpose}>{t(`purpose.${purpose}`)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-muted">{t("idle")}</p>
      )}
    </Card>
  );
}

export function ProcessorsPage() {
  const t = useTranslations("privacy");
  const processors = useProcessors();
  const data = processors.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      {processors.isLoading && <p className="text-sm text-text-muted">{t("loading")}</p>}
      {processors.isError && <p className="text-sm text-danger">{t("loadFailed")}</p>}
      {data && (
        <>
          <div className="grid gap-4 @3xl:grid-cols-2">
            {data.processors.map((p) => (
              <ProcessorCard key={p.name} p={p} />
            ))}
          </div>
          {data.processors.length === 0 && (
            <p className="text-sm">{t("none")}</p>
          )}
          {data.built_in.length > 0 && (
            <Card aria-label={t("builtIn")}>
              <h2 className="font-display text-lg font-semibold">{t("builtInTitle")}</h2>
              <ul className="list-disc pl-5 text-sm">
                {data.built_in.map((purpose) => (
                  <li key={purpose}>{t(`purpose.${purpose}`)}</li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
