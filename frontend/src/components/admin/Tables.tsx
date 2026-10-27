"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { LevelBadge } from "@/components/admin/LevelBadge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/components/ui/cn";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { EVENT_TYPES, isEventType, useAuditPage, type AuditEntry, type AuditFilters, type UserCoverage } from "@/lib/admin";

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
  const t = useTranslations("adminSecurity");
  return (
    <DataTable caption={t("usersByRisk")}>
      <THead>
        <Tr>
          <Th>{t("table.account")}</Th>
          <Th>{t("table.risk")}</Th>
          <Th align="right">{t("table.d1")}</Th>
          <Th align="right">{t("table.d7")}</Th>
          <Th align="right">{t("table.d30")}</Th>
          <Th align="right">{t("table.blocks7")}</Th>
          <Th>{t("table.state")}</Th>
        </Tr>
      </THead>
      <TBody>
        {users.map((u) => (
          <Tr key={u.user_id} selected={selected === u.user_id}>
            <Td primary>
              <button type="button" className="text-left hover:text-primary-strong" onClick={() => onSelect(u.user_id)}>
                {u.display_name ?? u.email ?? u.user_id.slice(0, 8)}
              </button>
              <a
                href={`/admin/users?user=${u.user_id}`}
                className="ml-2 text-xs text-text-muted hover:text-primary-strong"
                aria-label={t("table.accountLink", { name: u.display_name ?? u.email ?? u.user_id.slice(0, 8) })}
              >
                {t("table.accountShort")}
              </a>
            </Td>
            <Td label={t("table.risk")}>
              <LevelBadge level={u.level} /> <span className="text-xs text-text-muted">{u.risk.toFixed(2)}</span>
            </Td>
            <Td label={t("table.d1")} align="right">{pct(u.ratios["24h"] ?? 0)}</Td>
            <Td label={t("table.d7")} align="right">{pct(u.ratios["7d"] ?? 0)}</Td>
            <Td label={t("table.d30")} align="right">{pct(u.ratios["30d"] ?? 0)}</Td>
            <Td label={t("table.blocks7")} align="right">{u.blocks["7d"] ?? 0}</Td>
            <Td label={t("table.state")}>
              {u.is_locked ? (
                <span className="inline-flex items-center gap-2">
                  <span className="text-xs">{t("table.locked", { reason: u.locked_reason ?? "?" })}</span>
                  <Button variant="secondary" size="sm" onClick={() => onUnlock(u)}>
                    {t("table.unlock")}
                  </Button>
                </span>
              ) : (
                <span className="text-xs text-text-muted">{t("table.active")}</span>
              )}
            </Td>
          </Tr>
        ))}
      </TBody>
    </DataTable>
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

function useEventLabel() {
  const t = useTranslations("adminSecurity.events");
  return (type: string) => (isEventType(type) ? t(type) : type);
}

export function AlertsList({ alerts }: { alerts: AuditEntry[] }) {
  const t = useTranslations("adminSecurity");
  const format = useFormatter();
  const eventLabel = useEventLabel();
  if (alerts.length === 0) return <p className="text-sm text-text-muted">{t("noAlerts")}</p>;
  return (
    <ol className="flex flex-col gap-2 text-sm" aria-label={t("alerts")}>
      {alerts.map((a) => (
        <li key={a.id} className="flex flex-col gap-0.5 border-l-2 border-border pl-3">
          <span>
            <span className="font-medium">{eventLabel(a.event_type)}</span>{" "}
            <time dateTime={a.occurred_at} className="text-xs text-text-muted">
              {format.dateTime(new Date(a.occurred_at), "dateTimeSeconds")}
            </time>
          </span>
          <span className="text-xs text-text-muted">{t("alertAccount", { id: a.actor_id?.slice(0, 8) ?? "–" })}</span>
          <Payload payload={a.payload} />
        </li>
      ))}
    </ol>
  );
}

const PERIODS = [
  { key: "1", days: 1 },
  { key: "7", days: 7 },
  { key: "30", days: 30 },
  { key: "all", days: null },
] as const;

export function AuditLog({ initialActor = "" }: { initialActor?: string }) {
  const t = useTranslations("adminSecurity");
  const format = useFormatter();
  const eventLabel = useEventLabel();
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
        {t("audit.title")}
      </h2>
      <div className="flex flex-wrap items-end gap-3 text-sm" role="group" aria-label={t("audit.filters")}>
        <div className="flex gap-1" role="radiogroup" aria-label={t("audit.period")}>
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              role="radio"
              aria-checked={filters.sinceDays === p.days}
              onClick={() => change({ sinceDays: p.days })}
              className={cn(
                "rounded-lg border px-3 py-1.5",
                filters.sinceDays === p.days ? "border-primary font-medium" : "border-border text-text-muted",
              )}
            >
              {t(`periods.${p.key}`)}
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1">
          {t("audit.event")}
          <select
            value={filters.eventTypes[0] ?? ""}
            onChange={(event) => change({ eventTypes: event.target.value ? [event.target.value] : [] })}
            className="rounded-lg border border-border bg-surface px-2 py-1.5"
          >
            <option value="">{t("audit.allEvents")}</option>
            {EVENT_TYPES.map((value) => (
              <option key={value} value={value}>
                {eventLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("audit.actor")}
          <input
            value={filters.actor}
            onChange={(event) => change({ actor: event.target.value })}
            placeholder="UUID"
            className="w-full rounded-lg border border-border bg-surface px-2 py-1.5 font-mono text-xs @xl:w-80"
          />
        </label>
      </div>
      <div className={cn("transition-opacity", page.isFetching && page.isPlaceholderData && "opacity-50")}>
        <DataTable caption={t("audit.entries")}>
          <THead>
            <Tr>
              <Th>#</Th>
              <Th>{t("audit.time")}</Th>
              <Th>{t("audit.event")}</Th>
              <Th>{t("table.account")}</Th>
              <Th>{t("audit.details")}</Th>
            </Tr>
          </THead>
          <TBody>
            {page.data?.items.map((e) => (
              <Tr key={e.id}>
                <Td label="#" className="text-text-muted tabular-nums">
                  {e.id}
                </Td>
                <Td label={t("audit.time")} className="md:whitespace-nowrap">
                  {format.dateTime(new Date(e.occurred_at), "dateTimeSeconds")}
                </Td>
                <Td primary className="max-md:order-first">
                  {eventLabel(e.event_type)}
                </Td>
                <Td label={t("table.account")} className="font-mono text-xs">
                  {e.actor_id?.slice(0, 8) ?? "–"}
                </Td>
                <Td label={t("audit.details")} className="min-w-0 break-words">
                  <Payload payload={e.payload} />
                </Td>
              </Tr>
            ))}
          </TBody>
        </DataTable>
        {page.data?.items.length === 0 && <p className="py-3 text-sm text-text-muted">{t("audit.empty")}</p>}
      </div>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={cursors.length === 1} onClick={() => setCursors((c) => c.slice(0, -1))}>
          {t("audit.newer")}
        </Button>
        <Button
          variant="secondary"
          disabled={!page.data?.next_cursor}
          onClick={() => setCursors((c) => [...c, page.data?.next_cursor ?? null])}
        >
          {t("audit.older")}
        </Button>
      </div>
    </section>
  );
}
