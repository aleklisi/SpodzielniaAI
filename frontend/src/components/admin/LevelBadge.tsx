"use client";

import { useTranslations } from "next-intl";

import { LEVELS, type Level } from "@/lib/admin";

/** Risk level: status color on the icon only; the label says it in words (never color alone). */
export function LevelBadge({ level }: { level: string }) {
  const t = useTranslations("adminSecurity.levels");
  const key: Level = level in LEVELS ? (level as Level) : "NORMAL";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4" fill={LEVELS[key].color} />
      </svg>
      <span>{t(key)}</span>
    </span>
  );
}
