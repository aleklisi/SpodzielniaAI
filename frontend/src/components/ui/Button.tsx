import type { ButtonHTMLAttributes } from "react";

import { cn } from "./cn";

const VARIANTS = {
  primary: "bg-primary text-primary-contrast hover:bg-primary-strong",
  secondary: "border border-border bg-surface text-text hover:bg-surface-muted",
  danger: "bg-danger text-white hover:opacity-90",
  ghost: "text-text hover:bg-surface-muted",
} as const;

const SIZES = {
  md: "px-4 py-2 text-sm pointer-coarse:min-h-11",
  sm: "px-2.5 py-1 text-xs pointer-coarse:min-h-10",
  /** Square, for an icon alone: pass an `aria-label`. */
  icon: "size-10 p-0 pointer-coarse:size-11",
} as const;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANTS;
  size?: keyof typeof SIZES;
  busy?: boolean;
}

export function Button({ variant = "primary", size = "md", busy = false, className, disabled, children, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
    >
      {children}
    </button>
  );
}
