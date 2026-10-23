"use client";

import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { useAcknowledgeProcessors, useProcessors } from "@/lib/processors";
import { useMe } from "@/lib/session";

/** Once per member after an admin clears a new cloud provider for private data (P4). */
export function ProcessorsBanner() {
  const me = useMe();
  const processors = useProcessors(Boolean(me.data));
  const acknowledge = useAcknowledgeProcessors();
  if (!processors.data?.banner) return null;
  return (
    <div role="status" className="border-b border-border bg-surface-muted">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-2 text-sm">
        <span>
          Spółdzielnia dopuściła nowego dostawcę chmurowego do przetwarzania danych prywatnych.{" "}
          <Link href="/privacy" className="font-medium text-primary-strong underline">
            Gdzie przetwarzane są Twoje dane
          </Link>
        </span>
        <Button variant="secondary" busy={acknowledge.isPending} onClick={() => acknowledge.mutate()} className="ml-auto">
          Rozumiem
        </Button>
      </div>
    </div>
  );
}
