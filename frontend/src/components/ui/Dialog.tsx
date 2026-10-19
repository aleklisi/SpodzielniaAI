"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** A native modal `<dialog>`: focus trap, Escape and backdrop come from the browser. */
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", ""); // environments without <dialog> support (jsdom)
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="dialog-title"
      className="m-auto w-full max-w-lg rounded-xl border border-border bg-surface p-6 text-text backdrop:bg-black/50"
    >
      {open && (
        <>
          <h2 id="dialog-title" className="mb-4 font-display text-lg font-semibold">
            {title}
          </h2>
          {children}
        </>
      )}
    </dialog>
  );
}
