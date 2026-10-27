"use client";

import { useTranslations } from "next-intl";

import { cn } from "./cn";

/** A placeholder while data loads; the caller keeps a text status for screen readers (12.2). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-lg bg-surface-muted", className)} />;
}

/** The common "Loading…" state: a status line plus a few skeleton rows. */
export function Loading({ label, rows = 3 }: { label?: string; rows?: number }) {
  const t = useTranslations("common");
  return (
    <div className="flex flex-col gap-3">
      <p role="status" className="text-text-muted">
        {label ?? t("loading")}
      </p>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-14" />
      ))}
    </div>
  );
}
