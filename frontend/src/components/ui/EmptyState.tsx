import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/** What a list shows before it has anything in it (12.2). */
export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center">
      {Icon && <Icon aria-hidden className="size-8 text-text-muted" />}
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
