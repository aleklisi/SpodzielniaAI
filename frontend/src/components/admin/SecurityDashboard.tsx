"use client";

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
import { blocks } from "@/lib/plural";
import { useAlerts, useChain, useCoverage, useRiskHistory, type UserCoverage } from "@/lib/admin";

const PERIODS = [
  { label: "24 h", days: 1 },
  { label: "7 dni", days: 7 },
  { label: "30 dni", days: 30 },
];

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
  const [days, setDays] = useState(7);
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
        title="Bezpieczeństwo"
        description="Tarcza anty-destylacyjna, blokady kont i dziennik audytu."
        actions={
          <div className="flex gap-1 text-sm" role="radiogroup" aria-label="Okres alertów">
            {PERIODS.map((p) => (
              <button
                key={p.days}
                type="button"
                role="radio"
                aria-checked={days === p.days}
                onClick={() => setDays(p.days)}
                className={cn(
                  "rounded-lg border px-3 py-1.5 pointer-coarse:min-h-11",
                  days === p.days ? "border-primary font-medium" : "border-border text-text-muted",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        }
      />

      <div className="grid gap-3 @xl:grid-cols-2 @5xl:grid-cols-4" aria-label="Wskaźniki">
        <StatTile label="Zablokowane konta" value={String(users.filter((u) => u.is_locked).length)} note="stan bieżący" />
        <StatTile label="Pobrane kanarki" value={String(count("HONEYPOT_TRIGGERED"))} note={`w okresie ${PERIODS.find((p) => p.days === days)?.label}`} />
        <StatTile label="Zablokowane odpowiedzi dosłowne" value={String(count("VERBATIM_BLOCKED"))} note={`w okresie ${PERIODS.find((p) => p.days === days)?.label}`} />
        <StatTile
          label="Łańcuch dziennika audytu"
          value={chain.data ? (chain.data.ok ? "Nienaruszony" : "Naruszony") : "…"}
          status={chain.data ? (chain.data.ok ? "good" : "critical") : undefined}
          note={chain.data ? `${chain.data.rows} wpisów · ${chain.data.head_hash.slice(0, 12)}…` : undefined}
        />
      </div>

      <div className="grid gap-6 @5xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Card className="flex flex-col gap-4">
          {coverage.data && <CoverageHistogram users={users} />}
          <p className="text-xs text-text-muted">
            Korpus: {coverage.data ? blocks(coverage.data.corpus_blocks) : "…"} · limity: 24 h {pct(coverage.data?.limits["24h"])}, 7 dni{" "}
            {pct(coverage.data?.limits["7d"])}, 30 dni {pct(coverage.data?.limits["30d"])} · próg bezwzględny{" "}
            {coverage.data ? blocks(coverage.data.min_blocks_before_lock) : "…"}
          </p>
        </Card>
        {/* A scrolling region must be reachable by keyboard (axe: scrollable-region-focusable). */}
        <Card tabIndex={0} role="region" aria-labelledby="alerts-title" className="flex max-h-[28rem] flex-col gap-3 overflow-y-auto">
          <h2 id="alerts-title" className="font-display text-lg font-semibold">
            Alerty
          </h2>
          {alerts.data && <AlertsList alerts={alerts.data} />}
        </Card>
      </div>

      <section className="flex flex-col gap-3" aria-labelledby="users-title">
        <h2 id="users-title" className="font-display text-lg font-semibold">
          Konta według ryzyka
        </h2>
        {coverage.isPending && <p className="text-text-muted">Ładowanie…</p>}
        <UsersTable users={users} selected={selected} onSelect={setSelected} onUnlock={setUnlocking} />
      </section>

      {current && (
        <Card className="flex flex-col gap-4" aria-label="Szczegóły konta">
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
