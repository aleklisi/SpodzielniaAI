"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

/**
 * Debounced autosave of partial changes: `schedule(change)` merges `change` into what is
 * pending and saves `delayMs` after the last one (a title edit followed by a body edit saves
 * both). A pending change is flushed on unmount, so leaving the page loses nothing.
 */
export function useAutosave<T extends object>(save: (value: T) => Promise<unknown>, delayMs = 1000) {
  const [state, setState] = useState<SaveState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<{ value: T } | null>(null);
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const next = pending.current;
    if (!next) return;
    pending.current = null;
    setState("saving");
    try {
      await saveRef.current(next.value);
      setState(pending.current ? "pending" : "saved");
    } catch {
      setState("error");
    }
  }, []);

  const schedule = useCallback(
    (value: T) => {
      pending.current = { value: { ...pending.current?.value, ...value } };
      setState("pending");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), delayMs);
    },
    [delayMs, flush],
  );

  useEffect(() => () => void flush(), [flush]);
  return { state, schedule, flush };
}
