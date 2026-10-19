import type { HTMLAttributes } from "react";

import { cn } from "./cn";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={cn("rounded-xl border border-border bg-surface p-5 shadow-sm", className)} />;
}
