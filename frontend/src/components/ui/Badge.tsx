import type { HTMLAttributes } from "react";

import { cn } from "./cn";

export const TONES = {
  neutral: "bg-surface-muted text-text-muted",
  primary: "bg-primary/15 text-primary-strong",
  ok: "bg-ok/15 text-ok",
  warn: "bg-warn/15 text-warn",
  danger: "bg-danger/15 text-danger",
  accent: "bg-accent/15 text-accent",
} as const;

export type Tone = keyof typeof TONES;

export function Badge({ tone = "neutral", className, ...rest }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      {...rest}
      className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium", TONES[tone], className)}
    />
  );
}
