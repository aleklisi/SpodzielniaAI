import type { ButtonHTMLAttributes } from "react";

import { cn } from "./cn";

const VARIANTS = {
  primary: "bg-primary text-primary-contrast hover:bg-primary-strong",
  secondary: "border border-border bg-surface text-text hover:bg-surface-muted",
  danger: "bg-danger text-white hover:opacity-90",
  ghost: "text-text hover:bg-surface-muted",
} as const;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANTS;
  busy?: boolean;
}

export function Button({ variant = "primary", busy = false, className, disabled, children, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}
