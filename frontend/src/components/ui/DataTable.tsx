import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { cn } from "./cn";

/**
 * A table on md+, a list of cards below (12.2). One DOM for both: below md the table parts
 * become blocks, the header row is visually hidden and each `Td` shows its `label` above the
 * value. The explicit roles keep the table semantics that `display: block` would drop.
 */
export function DataTable({
  label,
  caption,
  className,
  children,
}: {
  /** The accessible name (aria-label) when there is no visible heading for the table. */
  label?: string;
  /** A visually hidden caption. */
  caption?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("md:overflow-x-auto", className)}>
      <table role="table" aria-label={label} className="w-full text-left text-sm max-md:block">
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead role="rowgroup" className="text-text-muted max-md:sr-only">
      {children}
    </thead>
  );
}

export function TBody({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <tbody role="rowgroup" className={cn("max-md:flex max-md:flex-col max-md:gap-3", className)}>
      {children}
    </tbody>
  );
}

export function Tr({ selected, className, ...rest }: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr
      role="row"
      {...rest}
      className={cn(
        "align-top [tbody>&]:border-t [tbody>&]:border-border",
        "max-md:flex max-md:flex-col max-md:gap-2 max-md:rounded-xl max-md:border max-md:border-border max-md:bg-surface max-md:p-4",
        selected && "bg-surface-muted max-md:border-primary",
        className,
      )}
    />
  );
}

export function Th({ align, className, ...rest }: ThHTMLAttributes<HTMLTableCellElement> & { align?: "right" }) {
  return (
    <th
      role="columnheader"
      scope="col"
      {...rest}
      className={cn("py-2 pr-3 font-medium last:pr-0", align === "right" && "text-right", className)}
    />
  );
}

export function Td({
  label,
  align,
  primary,
  className,
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & {
  /** The column name, shown before the value on a phone. */
  label?: string;
  align?: "right";
  /** The row's title: larger on a phone, with no label. */
  primary?: boolean;
}) {
  return (
    <td
      role="cell"
      data-label={label}
      {...rest}
      className={cn(
        "py-2 pr-3 last:pr-0 max-md:p-0",
        align === "right" && "md:text-right",
        // The label sits above the value: cells often hold several elements (badges, a select and a hint).
        label && "max-md:before:mb-0.5 max-md:before:block max-md:before:text-xs max-md:before:text-text-muted max-md:before:content-[attr(data-label)]",
        primary && "max-md:text-base max-md:font-medium",
        className,
      )}
    />
  );
}
