"use client";

import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";

import { cn } from "@/components/ui/cn";
import { api, ApiError, loginUrl } from "@/lib/api";
import { hasRole, useMe, type Role } from "@/lib/session";

const LINKS: Array<{ href: string; label: string; role?: Role }> = [
  { href: "/notebook", label: "Notatnik" },
  { href: "/wiki", label: "Wiki" },
  { href: "/atlas", label: "Atlas" },
  { href: "/chat", label: "Zapytaj spółdzielnię" },
  { href: "/me/drafts", label: "Moje szkice" },
  { href: "/me/contributions", label: "Moje kontrybucje" },
  { href: "/review", label: "Recenzje", role: "EXPERT" },
  { href: "/admin/security", label: "Bezpieczeństwo", role: "ADMIN" },
  { href: "/admin/llm", label: "Modele AI", role: "ADMIN" },
  { href: "/admin/users", label: "Użytkownicy", role: "ADMIN" },
  { href: "/admin/atlas", label: "Atlas (admin)", role: "ADMIN" },
];

/** Links for the account's roles (the API enforces them regardless). */
export function MainNav() {
  const me = useMe();
  const path = usePathname();
  const router = useRouter();
  const client = useQueryClient();
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  return (
    <nav aria-label="Główna" className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
      <Link href="/" className="font-display text-lg font-semibold text-primary-strong">
        Spółdzielnia AI
      </Link>
      {me.data &&
        LINKS.filter((l) => !l.role || hasRole(me.data, l.role)).map((l) => (
          <Link
            key={l.href}
            href={l.href}
            aria-current={path.startsWith(l.href) ? "page" : undefined}
            className={cn("text-sm hover:text-text", path.startsWith(l.href) ? "font-medium text-text" : "text-text-muted")}
          >
            {l.label}
          </Link>
        ))}
      <span className="ml-auto text-sm">
        {me.data && (
          <button
            type="button"
            className="text-text-muted hover:text-text"
            onClick={async () => {
              await api.POST("/auth/logout").catch(() => undefined);
              client.clear(); // nothing of the previous account stays in memory
              router.push("/");
            }}
          >
            Wyloguj ({me.data.display_name ?? me.data.email ?? "konto"})
          </button>
        )}
        {signedOut && (
          <a href={loginUrl(path)} className="font-medium text-primary-strong">
            Zaloguj się
          </a>
        )}
      </span>
    </nav>
  );
}
