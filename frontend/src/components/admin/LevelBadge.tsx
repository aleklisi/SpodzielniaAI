import { LEVELS, type Level } from "@/lib/admin";

/** Risk level: status color on the icon only; the label says it in words (never color alone). */
export function LevelBadge({ level }: { level: string }) {
  const known = LEVELS[level as Level] ?? LEVELS.NORMAL;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4" fill={known.color} />
      </svg>
      <span>{known.label}</span>
    </span>
  );
}
