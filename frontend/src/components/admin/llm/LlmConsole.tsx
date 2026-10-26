"use client";

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
import { PURPOSES, useLlmStatus, useModels, useProviders, useRevisions, type LlmStatus } from "@/lib/llm-admin";

const TABS = [
  { key: "routing", label: "Przypisania" },
  { key: "providers", label: "Dostawcy" },
  { key: "models", label: "Modele" },
  { key: "index", label: "Indeksy" },
  { key: "playground", label: "Playground" },
  { key: "history", label: "Historia" },
] as const;

type Tab = (typeof TABS)[number]["key"];

/**
 * Which LLM serves which part of the system (Stage 7). Changes go into a draft revision, are
 * tested, then activated without a restart; any earlier revision can be restored.
 */
export function LlmConsole() {
  const [tab, setTab] = useState<Tab>("routing");
  const status = useLlmStatus();
  const providers = useProviders();
  const models = useModels();
  const revisions = useRevisions();

  const stepUp = [status.error, providers.error, models.error, revisions.error].find((e) => e instanceof StepUpRequired);
  if (stepUp instanceof StepUpRequired) return <StepUpNotice stepUp={stepUp.stepUp} />;
  if (status.isPending || providers.isPending || models.isPending || revisions.isPending) {
    return <p className="text-text-muted">Ładowanie…</p>;
  }
  if (!status.data || !providers.data || !models.data || !revisions.data) {
    return (
      <Card role="alert" className="max-w-xl">
        Nie udało się wczytać konfiguracji modeli.
      </Card>
    );
  }
  const s = status.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Modele AI"
        description="Które modele obsługują poszczególne funkcje. Zmiany wchodzą w życie bez restartu, po teście i aktywacji."
        actions={
          <div className="flex flex-wrap gap-2 text-sm" aria-label="Stan konfiguracji">
            <Badge tone="primary">Aktywna rewizja: {s.active_revision ?? "—"}</Badge>
            {s.process_revision !== s.active_revision && (
              <Badge tone="warn" title="Ten proces API jeszcze nie przeładował konfiguracji">
                Serwer używa: {s.process_revision === 0 ? ".env" : `rewizji ${s.process_revision}`}
              </Badge>
            )}
            <Badge tone={s.allow_cloud ? "warn" : "ok"}>{s.allow_cloud ? "Chmura dozwolona" : "Tylko modele lokalne"}</Badge>
            {s.config_source === "env" && <Badge tone="danger">Tryb .env: zmiany z panelu są ignorowane</Badge>}
          </div>
        }
      />

      <MissingModels status={s} />

      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Sekcje" />

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
  if (status.missing_purposes.length === 0) return null;
  return (
    <Card role="region" aria-label="Brakujące modele" className="flex flex-col gap-2 border-warn text-sm">
      <h2 className="font-medium">Brakujące modele</h2>
      <p className="text-text-muted">
        Te zastosowania nie mają modelu, więc ich funkcje są wyłączone dla wszystkich. Materiały członków czekają i zostaną
        przetworzone automatycznie po aktywacji konfiguracji z odpowiednim modelem.
      </p>
      <ul className="list-disc pl-5">
        {status.missing_purposes.map((p) => (
          <li key={p}>
            {PURPOSES[p].label} <span className="text-text-muted">— {PURPOSES[p].hint}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
