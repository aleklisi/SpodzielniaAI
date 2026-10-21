"use client";

import { useId, useState } from "react";

import { cn } from "@/components/ui/cn";
import { atlasError, useLabels, useProposeLabel, type Label } from "@/lib/atlas";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm";

/**
 * Picks a label of one domain (WAI-ARIA combobox with a listbox popup). Search matches names and
 * synonyms without diacritics. When nothing fits, the member proposes the typed name; the
 * proposal is usable on their own photos at once and goes to the experts.
 */
export function LabelCombobox({
  domainId,
  value,
  onChange,
  label = "Etykieta",
  disabled = false,
}: {
  domainId: string;
  value: Pick<Label, "id" | "name" | "status"> | null;
  onChange: (label: Label) => void;
  label?: string;
  disabled?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const labels = useLabels(open ? domainId : null, q.trim());
  const propose = useProposeLabel();
  const id = useId();
  const listId = `${id}-list`;

  const options = labels.data ?? [];
  const typed = q.trim();
  const exact = options.some((o) => o.name.toLocaleLowerCase("pl") === typed.toLocaleLowerCase("pl"));
  const canPropose = typed.length > 0 && !exact && !labels.isFetching;
  const count = options.length + (canPropose ? 1 : 0);

  function choose(option: Label) {
    onChange(option);
    setQ("");
    setOpen(false);
    setError(null);
  }

  async function proposeTyped() {
    setError(null);
    try {
      choose(await propose.mutateAsync({ domain_id: domainId, name: typed }));
    } catch (e) {
      setError(atlasError(e, "Nie udało się zaproponować etykiety."));
    }
  }

  function pick(index: number) {
    if (index < options.length) choose(options[index]);
    else if (canPropose) void proposeTyped();
  }

  return (
    <div className="relative flex flex-col gap-1 text-sm">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && count > 0 ? `${id}-opt-${active}` : undefined}
        className={INPUT}
        disabled={disabled}
        autoComplete="off"
        placeholder={value ? value.name : "Szukaj etykiety…"}
        value={q}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, Math.max(count - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && count > 0) {
            e.preventDefault();
            pick(active);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {value && (
        <p className="text-xs text-text-muted">
          Wybrano: <span className="font-medium text-text">{value.name}</span>
          {value.status === "PROPOSED" && " (propozycja czeka na ekspertów)"}
          {value.status === "DEPRECATED" && " (etykieta wycofana)"}
        </p>
      )}
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={label}
          className="absolute top-full z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-border bg-surface shadow-lg"
        >
          {options.map((o, i) => (
            <li
              key={o.id}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              className={cn("cursor-pointer px-3 py-2", i === active && "bg-surface-muted")}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(o)}
            >
              <span className="font-medium">{o.name}</span>
              {o.status === "PROPOSED" && <span className="ml-2 text-xs text-warn">propozycja</span>}
              {o.synonyms.length > 0 && <span className="block text-xs text-text-muted">{o.synonyms.join(", ")}</span>}
            </li>
          ))}
          {canPropose && (
            <li
              id={`${id}-opt-${options.length}`}
              role="option"
              aria-selected={active === options.length}
              className={cn("cursor-pointer px-3 py-2 text-primary-strong", active === options.length && "bg-surface-muted")}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => void proposeTyped()}
            >
              Zaproponuj nową etykietę „{typed}”
            </li>
          )}
          {count === 0 && !labels.isFetching && <li className="px-3 py-2 text-text-muted">Wpisz nazwę, aby wyszukać.</li>}
        </ul>
      )}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
