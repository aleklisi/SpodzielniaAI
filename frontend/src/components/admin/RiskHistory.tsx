"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState, type PointerEvent } from "react";

import type { AuditEntry } from "@/lib/admin";

const W = 560;
const H = 200;
const M = { top: 12, right: 72, bottom: 24, left: 32 };
const THRESHOLDS = [
  { value: 0.3, level: "ELEVATED" },
  { value: 0.5, level: "HIGH" },
  { value: 0.7, level: "CRITICAL" },
] as const;

export interface RiskPoint {
  at: number;
  score: number;
  level: string;
}

export function riskPoints(entries: AuditEntry[]): RiskPoint[] {
  return entries
    .map((e) => ({ at: Date.parse(e.occurred_at), score: Number(e.payload.score), level: String(e.payload.level ?? "") }))
    .filter((p) => Number.isFinite(p.at) && Number.isFinite(p.score))
    .sort((a, b) => a.at - b.at);
}

/** One account's risk score at each recorded shield decision (ELEVATED and above). */
export function RiskHistory({ entries }: { entries: AuditEntry[] }) {
  const t = useTranslations("adminSecurity");
  const format = useFormatter();
  const points = riskPoints(entries);
  const [active, setActive] = useState<number | null>(null);
  if (points.length === 0) return <p className="text-sm text-text-muted">{t("risk.none")}</p>;

  const t0 = points[0].at;
  const t1 = Math.max(points[points.length - 1].at, t0 + 1);
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const x = (t: number) => M.left + (points.length === 1 ? plotW / 2 : ((t - t0) / (t1 - t0)) * plotW);
  const y = (v: number) => M.top + plotH - v * plotH;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.at).toFixed(1)},${y(p.score).toFixed(1)}`).join(" ");

  function nearest(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!box) return;
    const px = ((event.clientX - box.left) / box.width) * W;
    let best = 0;
    for (let i = 1; i < points.length; i++) {
      if (Math.abs(x(points[i].at) - px) < Math.abs(x(points[best].at) - px)) best = i;
    }
    setActive(best);
  }

  const shown = active === null ? null : points[active];
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="text-sm font-medium">{t("risk.title")}</figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="group" aria-label={t("risk.label")}>
          {THRESHOLDS.map((threshold) => (
            <g key={threshold.value}>
              <line x1={M.left} x2={W - M.right} y1={y(threshold.value)} y2={y(threshold.value)} stroke="var(--viz-grid)" strokeWidth={1} />
              <text x={W - M.right + 6} y={y(threshold.value)} dominantBaseline="middle" fontSize={11} className="fill-text-muted">
                {t(`levels.${threshold.level}`)}
              </text>
            </g>
          ))}
          {[0, 1].map((v) => (
            <text key={v} x={M.left - 6} y={y(v)} textAnchor="end" dominantBaseline="middle" fontSize={11} className="fill-text-muted tabular-nums">
              {v}
            </text>
          ))}
          <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--viz-axis)" strokeWidth={1} />
          {shown && <line x1={x(shown.at)} x2={x(shown.at)} y1={M.top} y2={y(0)} stroke="var(--viz-axis)" strokeWidth={1} />}
          <path d={line} fill="none" stroke="var(--viz-series)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {points.map((p, i) => (
            <circle key={i} cx={x(p.at)} cy={y(p.score)} r={active === i ? 5 : 4} fill="var(--viz-series)" stroke="var(--surface)" strokeWidth={2} />
          ))}
          {/* Crosshair: the pointer only needs to be nearest in time, not on a dot. */}
          <rect
            x={M.left}
            y={M.top}
            width={plotW}
            height={plotH}
            fill="transparent"
            tabIndex={0}
            aria-label={t("risk.explore")}
            onPointerMove={nearest}
            onPointerLeave={() => setActive(null)}
            onFocus={() => setActive(points.length - 1)}
            onBlur={() => setActive(null)}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft") setActive((a) => Math.max(0, (a ?? 0) - 1));
              if (event.key === "ArrowRight") setActive((a) => Math.min(points.length - 1, (a ?? 0) + 1));
            }}
          />
        </svg>
        {shown && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow"
            ref={(el) => {
              if (el) el.style.left = `${Math.min((x(shown.at) / W) * 100, 75)}%`;
            }}
          >
            <div className="text-sm font-semibold tabular-nums">{shown.score.toFixed(2)}</div>
            <div className="text-text-muted">
              {shown.level} · {format.dateTime(shown.at, "dateTimeShort")}
            </div>
          </div>
        )}
      </div>
    </figure>
  );
}
