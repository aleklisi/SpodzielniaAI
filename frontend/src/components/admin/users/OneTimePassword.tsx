"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

/**
 * A temporary password, shown once. It lives only in the parent's state and disappears when the
 * dialog closes; the server never stores it (U1).
 */
export function OneTimePassword({ password, username }: { password: string; username: string | null }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Card role="status" className="flex flex-col gap-3 border-warn p-4 text-sm">
      <p>
        Hasło tymczasowe{username ? ` dla ${username}` : ""}. Pokazujemy je <strong>tylko raz</strong>: przekaż je
        użytkownikowi bezpiecznym kanałem (nie e-mailem). Przy logowaniu ustawi własne hasło.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <output
          aria-label="Hasło tymczasowe"
          className="rounded-lg border border-border bg-surface-muted px-3 py-2 font-mono text-base tracking-wide select-all"
        >
          {password}
        </output>
        <Button variant="secondary" onClick={() => void copy()}>
          {copied ? "Skopiowano" : "Kopiuj"}
        </Button>
      </div>
    </Card>
  );
}
