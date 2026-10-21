"use client";

import { useState } from "react";

import { LevelBadge } from "@/components/admin/LevelBadge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/components/ui/cn";
import { EVENT_LABELS, useAuditPage, type AuditEntry, type AuditFilters, type UserCoverage } from "@/lib/admin";

const TIME = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "medium" });
const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export function UsersTable({
  users,
  selected,
  onSelect,
  onUnlock,
}: {
  users: UserCoverage[];
  selected: string | null;
  onSelect: (id: string) => void;
  onUnlock: (user: UserCoverage) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Konta według ryzyka</caption>
        <thead className="text-text-muted">
          <tr>
            <th className="py-2 pr-3 font-medium">Konto</th>
            <th className="py-2 pr-3 font-medium">Ryzyko</th>
            <th className="py-2 pr-3 text-right font-medium">24 h</th>
            <th className="py-2 pr-3 text-right font-medium">7 dni</th>
            <th className="py-2 pr-3 text-right font-medium">30 dni</th>
            <th className="py-2 pr-3 text-right font-medium">Bloki (7 dni)</th>
            <th className="py-2 font-medium">Stan</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {users.map((u) => (
            <tr key={u.user_id} className={cn("border-t border-border", selected === u.user_id && "bg-surface-muted")}>
              <td className="py-2 pr-3">
                <button type="button" className="text-left hover:text-primary-strong" onClick={() => onSelect(u.user_id)}>
                  {u.display_name ?? u.email ?? u.user_id.slice(0, 8)}
                </button>
                <a
                  href={`/admin/users?user=${u.user_id}`}
                  className="ml-2 text-xs text-text-muted hover:text-primary-strong"
                  aria-label={`Konto: ${u.display_name ?? u.email ?? u.user_id.slice(0, 8)}`}
                >
                  konto
                </a>
              </td>
              <td className="py-2 pr-3">
                <LevelBadge level={u.level} /> <span className="text-xs text-text-muted">{u.risk.toFixed(2)}</span>
              </td>
              <td className="py-2 pr-3 text-right">{pct(u.ratios["24h"] ?? 0)}</td>
              <td className="py-2 pr-3 text-right">{pct(u.ratios["7d"] ?? 0)}</td>
              <td className="py-2 pr-3 text-right">{pct(u.ratios["30d"] ?? 0)}</td>
              <td className="py-2 pr-3 text-right">{u.blocks["7d"] ?? 0}</td>
              <td className="py-2">
                {u.is_locked ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="text-xs">Zablokowane ({u.locked_reason ?? "?"})</span>
                    <Button variant="secondary" className="px-2 py-1 text-xs" onClick={() => onUnlock(u)}>
                      Odblokuj
                    </Button>
                  </span>
                ) : (
                  <span className="text-xs text-text-muted">aktywne</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Payload({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(([, v]) => typeof v !== "object" || v === null);
  return (
    <span className="text-xs text-text-muted">
      {entries.map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
    </span>
  );
}

export function AlertsList({ alerts }: { alerts: AuditEntry[] }) {
  if (alerts.length === 0) return <p className="text-sm text-text-muted">Brak alertów w wybranym okresie.</p>;
  return (
    <ol className="flex flex-col gap-2 text-sm" aria-label="Alerty">
      {alerts.map((a) => (
        <li key={a.id} className="flex flex-col gap-0.5 border-l-2 border-border pl-3">
          <span>
            <span className="font-medium">{EVENT_LABELS[a.event_type] ?? a.event_type}</span>{" "}
            <time dateTime={a.occurred_at} className="text-xs text-text-muted">
              {TIME.format(new Date(a.occurred_at))}
            </time>
          </span>
          <span className="text-xs text-text-muted">konto {a.actor_id?.slice(0, 8) ?? "–"}</span>
          <Payload payload={a.payload} />
        </li>
      ))}
    </ol>
  );
}

const PERIODS: Array<{ label: string; days: number | null }> = [
  { label: "24 h", days: 1 },
  { label: "7 dni", days: 7 },
  { label: "30 dni", days: 30 },
  { label: "Wszystko", days: null },
];

export function AuditLog({ initialActor = "" }: { initialActor?: string }) {
  const [filters, setFilters] = useState<AuditFilters>({ eventTypes: [], actor: initialActor, sinceDays: 7 });
  const [cursors, setCursors] = useState<Array<number | null>>([null]);
  const cursor = cursors[cursors.length - 1] ?? null;
  const page = useAuditPage(filters, cursor);
  const change = (next: Partial<AuditFilters>) => {
    setFilters((f) => ({ ...f, ...next }));
    setCursors([null]);
  };

  return (
    <section className="flex flex-col gap-3" aria-labelledby="audit-title">
      <h2 id="audit-title" className="font-display text-lg font-semibold">
        Dziennik audytu
      </h2>
      <div className="flex flex-wrap items-end gap-3 text-sm" role="group" aria-label="Filtry dziennika">
        <div className="flex gap-1" role="radiogroup" aria-label="Okres">
          {PERIODS.map((p) => (
            <button
              key={p.label}
              type="button"
              role="radio"
              aria-checked={filters.sinceDays === p.days}
              onClick={() => change({ sinceDays: p.days })}
              className={cn(
                "rounded-lg border px-3 py-1.5",
                filters.sinceDays === p.days ? "border-primary font-medium" : "border-border text-text-muted",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1">
          Zdarzenie
          <select
            value={filters.eventTypes[0] ?? ""}
            onChange={(event) => change({ eventTypes: event.target.value ? [event.target.value] : [] })}
            className="rounded-lg border border-border bg-surface px-2 py-1.5"
          >
            <option value="">wszystkie</option>
            {Object.entries(EVENT_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Konto (ID)
          <input
            value={filters.actor}
            onChange={(event) => change({ actor: event.target.value })}
            placeholder="UUID"
            className="w-80 rounded-lg border border-border bg-surface px-2 py-1.5 font-mono text-xs"
          />
        </label>
      </div>
      <div className={cn("overflow-x-auto transition-opacity", page.isFetching && page.isPlaceholderData && "opacity-50")}>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Wpisy dziennika audytu</caption>
          <thead className="text-text-muted">
            <tr>
              <th className="py-2 pr-3 font-medium">#</th>
              <th className="py-2 pr-3 font-medium">Czas</th>
              <th className="py-2 pr-3 font-medium">Zdarzenie</th>
              <th className="py-2 pr-3 font-medium">Konto</th>
              <th className="py-2 font-medium">Szczegóły</th>
            </tr>
          </thead>
          <tbody>
            {page.data?.items.map((e) => (
              <tr key={e.id} className="border-t border-border align-top">
                <td className="py-2 pr-3 tabular-nums text-text-muted">{e.id}</td>
                <td className="py-2 pr-3 whitespace-nowrap">{TIME.format(new Date(e.occurred_at))}</td>
                <td className="py-2 pr-3">{EVENT_LABELS[e.event_type] ?? e.event_type}</td>
                <td className="py-2 pr-3 font-mono text-xs">{e.actor_id?.slice(0, 8) ?? "–"}</td>
                <td className="py-2">
                  <Payload payload={e.payload} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {page.data?.items.length === 0 && <p className="py-3 text-sm text-text-muted">Brak wpisów.</p>}
      </div>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={cursors.length === 1} onClick={() => setCursors((c) => c.slice(0, -1))}>
          Nowsze
        </Button>
        <Button
          variant="secondary"
          disabled={!page.data?.next_cursor}
          onClick={() => setCursors((c) => [...c, page.data?.next_cursor ?? null])}
        >
          Starsze
        </Button>
      </div>
    </section>
  );
}
