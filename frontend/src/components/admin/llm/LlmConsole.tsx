"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { HistoryPanel } from "@/components/admin/llm/History";
import { IndexPanel } from "@/components/admin/llm/IndexPanel";
import { PlaygroundPanel } from "@/components/admin/llm/Playground";
import { ModelsPanel, ProvidersPanel } from "@/components/admin/llm/Catalog";
import { RoutingPanel } from "@/components/admin/llm/Routing";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabPanel, Tabs } from "@/components/ui/Tabs";
import { StepUpRequired } from "@/lib/api";
import { useLlmStatus, useModels, useProviders, useRevisions, type LlmStatus } from "@/lib/llm-admin";

const TABS = ["routing", "providers", "models", "index", "playground", "history"] as const;

type Tab = (typeof TABS)[number];

/**
 * Which LLM serves which part of the system (Stage 7). Changes go into a draft revision, are
 * tested, then activated without a restart; any earlier revision can be restored.
 */
export function LlmConsole() {
  const t = useTranslations("adminLlm");
  const [tab, setTab] = useState<Tab>("routing");
  const status = useLlmStatus();
  const providers = useProviders();
  const models = useModels();
  const revisions = useRevisions();

  const stepUp = [status.error, providers.error, models.error, revisions.error].find((e) => e instanceof StepUpRequired);
  if (stepUp instanceof StepUpRequired) return <StepUpNotice stepUp={stepUp.stepUp} />;
  if (status.isPending || providers.isPending || models.isPending || revisions.isPending) {
    return <p className="text-text-muted">{t("loading")}</p>;
  }
  if (!status.data || !providers.data || !models.data || !revisions.data) {
    return (
      <Card role="alert" className="max-w-xl">
        {t("loadFailed")}
      </Card>
    );
  }
  const s = status.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex flex-wrap gap-2 text-sm" aria-label={t("state")}>
            <Badge tone="primary">{t("activeRevision", { number: s.active_revision ?? "—" })}</Badge>
            {s.process_revision !== s.active_revision && (
              <Badge tone="warn" title={t("notReloaded")}>
                {t("serverUses", { source: s.process_revision === 0 ? ".env" : t("revisionN", { number: s.process_revision }) })}
              </Badge>
            )}
            <Badge tone={s.allow_cloud ? "warn" : "ok"}>{s.allow_cloud ? t("cloudAllowed") : t("localOnly")}</Badge>
            {s.config_source === "env" && <Badge tone="danger">{t("envMode")}</Badge>}
          </div>
        }
      />

      <MissingModels status={s} />

      <Tabs tabs={TABS.map((key) => ({ key, label: t(`tabs.${key}`) }))} value={tab} onChange={setTab} label={t("sections")} />

      <TabPanel tab={tab} className="min-w-0">
        {tab === "routing" && (
          <RoutingPanel status={s} providers={providers.data} models={models.data} revisions={revisions.data} />
        )}
        {tab === "providers" && <ProvidersPanel status={s} providers={providers.data} models={models.data} />}
        {tab === "models" && <ModelsPanel status={s} providers={providers.data} models={models.data} />}
        {tab === "index" && <IndexPanel />}
        {tab === "playground" && <PlaygroundPanel status={s} providers={providers.data} models={models.data} />}
        {tab === "history" && <HistoryPanel revisions={revisions.data} />}
      </TabPanel>
    </div>
  );
}

/** Purposes without a usable model: their features are off for everyone (10.2). */
export function MissingModels({ status }: { status: LlmStatus }) {
  const t = useTranslations("adminLlm");
  if (status.missing_purposes.length === 0) return null;
  return (
    <Card role="region" aria-label={t("missing.title")} className="flex flex-col gap-2 border-warn text-sm">
      <h2 className="font-medium">{t("missing.title")}</h2>
      <p className="text-text-muted">{t("missing.body")}</p>
      <ul className="list-disc pl-5">
        {status.missing_purposes.map((p) => (
          <li key={p}>
            {t(`purposes.${p}.label`)} <span className="text-text-muted">— {t(`purposes.${p}.hint`)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
