import type { ReactNode } from "react";

import { cn } from "./cn";

/** A page's title, one-line description and actions; the actions wrap below on a phone (12.2). */
export function PageHeader({
  title,
  description,
  actions,
  back,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** A link back to the parent screen, shown above the title. */
  back?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("flex flex-col gap-3", className)}>
      {back && <div className="text-sm text-text-muted">{back}</div>}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold break-words md:text-3xl">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-sm text-text-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2 sm:shrink-0 sm:justify-end">{actions}</div>}
      </div>
    </header>
  );
}
