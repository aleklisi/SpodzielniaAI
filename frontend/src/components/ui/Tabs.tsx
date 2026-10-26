"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "./cn";

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  icon?: ReactNode;
}

/**
 * WAI-ARIA tabs (12.2): arrow keys, Home and End move between tabs (automatic activation); only
 * the selected tab is in the tab order. Scrolls sideways on a phone instead of wrapping.
 * Pair with `TabPanel` using the same `idPrefix`.
 */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
  idPrefix = "tab",
  panelPrefix = idPrefix,
  fill = false,
  className,
}: {
  tabs: ReadonlyArray<TabItem<K>>;
  value: K;
  onChange: (key: K) => void;
  label: string;
  idPrefix?: string;
  /** The panels' id prefix, when two tab lists control the same panels. */
  panelPrefix?: string;
  /** Tabs share the width equally (the notebook's phone tabs). */
  fill?: boolean;
  className?: string;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const last = tabs.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (next === null) return;
    event.preventDefault();
    onChange(tabs[next]!.key);
    refs.current[next]?.focus();
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("flex gap-1 overflow-x-auto border-b border-border [scrollbar-width:none]", className)}
    >
      {tabs.map((tab, index) => {
        const selected = tab.key === value;
        return (
          <button
            key={tab.key}
            ref={(element) => {
              refs.current[index] = element;
            }}
            id={`${idPrefix}-${tab.key}`}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={`${panelPrefix}-panel-${tab.key}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.key)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              "-mb-px inline-flex shrink-0 items-center justify-center gap-2 border-b-2 px-4 py-2 text-sm whitespace-nowrap pointer-coarse:min-h-11",
              fill && "flex-1",
              selected ? "border-primary font-medium text-text" : "border-transparent text-text-muted hover:text-text",
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  tab,
  idPrefix = "tab",
  hidden,
  className,
  children,
}: {
  tab: string;
  idPrefix?: string;
  /** Keep the panel mounted but hidden (e.g. an editor with unsaved state). */
  hidden?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      role="tabpanel"
      id={`${idPrefix}-panel-${tab}`}
      aria-labelledby={`${idPrefix}-${tab}`}
      hidden={hidden}
      className={className}
    >
      {children}
    </section>
  );
}
