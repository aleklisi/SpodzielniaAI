import { SearchX } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/ui/EmptyState";

// Next's built-in 404 page uses inline style attributes, which the CSP (style-src nonce) blocks.
export default function NotFound() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="font-display text-2xl font-semibold">Nie znaleziono strony</h1>
      <EmptyState
        icon={SearchX}
        title="Ta strona nie istnieje albo nie masz do niej dostępu."
        action={
          <Link href="/" className="font-medium text-primary-strong underline">
            Wróć na stronę główną
          </Link>
        }
      />
    </div>
  );
}
