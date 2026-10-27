"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { CoverageHistogram } from "@/components/admin/CoverageHistogram";
import { RiskHistory } from "@/components/admin/RiskHistory";
import { AlertsList, AuditLog, UsersTable } from "@/components/admin/Tables";
import { UnlockDialog } from "@/components/admin/UnlockDialog";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { PageHeader } from "@/components/ui/PageHeader";
import { StepUpRequired } from "@/lib/api";
import { useAlerts, useChain, useCoverage, useRiskHistory, type UserCoverage } from "@/lib/admin";

const PERIODS = [1, 7, 30] as const;

function StatTile({ label, value, note, status }: { label: string; value: string; note?: string; status?: "good" | "critical" }) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="flex items-center gap-2 text-2xl font-semibold">
        {status && (
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <circle cx="6" cy="6" r="5" fill={status === "good" ? "var(--status-good)" : "var(--status-critical)"} />
          </svg>
        )}
        {value}
      </span>
      {note && <span className="text-xs text-text-muted">{note}</span>}
    </Card>
  );
}

/**
 * The cooperative's security console (6.6). Everything here is read from the API as ADMIN
 * with MFA, and each read is recorded in the audit log by the backend.
 */
export function SecurityDashboard() {
  const t = useTranslations("adminSecurity");
  const [days, setDays] = useState<(typeof PERIODS)[number]>(7);
  const [selected, setSelected] = useState<string | null>(null);
  const [unlocking, setUnlocking] = useState<UserCoverage | null>(null);
  const coverage = useCoverage();
  const alerts = useAlerts(days);
  const chain = useChain();
  const history = useRiskHistory(selected);

  const stepUp = [coverage.error, alerts.error, chain.error].find((e) => e instanceof StepUpRequired);
  if (stepUp instanceof StepUpRequired) return <StepUpNotice stepUp={stepUp.stepUp} />;

  const users = coverage.data?.users ?? [];
  const count = (type: string) => (alerts.data ?? []).filter((a) => a.event_type === type).length;
  const current = users.find((u) => u.user_id === selected) ?? null;

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex gap-1 text-sm" role="radiogroup" aria-label={t("alertPeriod")}>
            {PERIODS.map((p) => (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={days === p}
                onClick={() => setDays(p)}
                className={cn(
                  "rounded-lg border px-3 py-1.5 pointer-coarse:min-h-11",
                  days === p ? "border-primary font-medium" : "border-border text-text-muted",
                )}
              >
                {t(`periods.${p}`)}
              </button>
            ))}
          </div>
        }
      />

      <div className="grid gap-3 @xl:grid-cols-2 @5xl:grid-cols-4" aria-label={t("indicators")}>
        <StatTile label={t("lockedAccounts")} value={String(users.filter((u) => u.is_locked).length)} note={t("current")} />
        <StatTile label={t("honeypots")} value={String(count("HONEYPOT_TRIGGERED"))} note={t("inPeriod", { period: t(`periods.${days}`) })} />
        <StatTile label={t("verbatim")} value={String(count("VERBATIM_BLOCKED"))} note={t("inPeriod", { period: t(`periods.${days}`) })} />
        <StatTile
          label={t("chain")}
          value={chain.data ? (chain.data.ok ? t("intact") : t("broken")) : "…"}
          status={chain.data ? (chain.data.ok ? "good" : "critical") : undefined}
          note={chain.data ? t("chainNote", { rows: chain.data.rows, hash: chain.data.head_hash.slice(0, 12) }) : undefined}
        />
      </div>

      <div className="grid gap-6 @5xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="flex flex-col gap-4">
          {coverage.data && <CoverageHistogram users={users} />}
          <p className="text-xs text-text-muted">
            {t("corpus", {
              corpus: coverage.data ? t("blocks", { count: coverage.data.corpus_blocks }) : "…",
              d1: pct(coverage.data?.limits["24h"]),
              d7: pct(coverage.data?.limits["7d"]),
              d30: pct(coverage.data?.limits["30d"]),
              floor: coverage.data ? t("blocks", { count: coverage.data.min_blocks_before_lock }) : "…",
            })}
          </p>
        </Card>
        {/* A scrolling region must be reachable by keyboard (axe: scrollable-region-focusable). */}
        <Card tabIndex={0} role="region" aria-labelledby="alerts-title" className="flex max-h-[28rem] flex-col gap-3 overflow-y-auto">
          <h2 id="alerts-title" className="font-display text-lg font-semibold">
            {t("alerts")}
          </h2>
          {alerts.data && <AlertsList alerts={alerts.data} />}
        </Card>
      </div>

      <section className="flex flex-col gap-3" aria-labelledby="users-title">
        <h2 id="users-title" className="font-display text-lg font-semibold">
          {t("usersByRisk")}
        </h2>
        {coverage.isPending && <p className="text-text-muted">{t("loading")}</p>}
        <UsersTable users={users} selected={selected} onSelect={setSelected} onUnlock={setUnlocking} />
      </section>

      {current && (
        <Card className="flex flex-col gap-4" aria-label={t("accountDetails")}>
          <h2 className="font-display text-lg font-semibold">{current.display_name ?? current.email ?? current.user_id}</h2>
          <RiskHistory entries={history.data?.items ?? []} />
          <AuditLog key={current.user_id} initialActor={current.user_id} />
        </Card>
      )}

      {!current && <AuditLog />}
      <UnlockDialog user={unlocking} onClose={() => setUnlocking(null)} />
    </div>
  );
}

function pct(value: number | undefined): string {
  return value === undefined ? "…" : `${Math.round(value * 100)}%`;
}
