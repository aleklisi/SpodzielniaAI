"use client";

import { LogIn } from "lucide-react";
import Link from "next/link";

import { visibleSections } from "@/components/shell/nav-items";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Skeleton";
import { ApiError, loginUrl } from "@/lib/api";
import { useMe } from "@/lib/session";

const TEXT: Record<string, string> = {
  "/notebook": "Twoje notatki, zdjęcia i nagrania. Widzisz je tylko Ty.",
  "/me/drafts": "Artykuły z Twoich wniosków, zanim zatwierdzi je Rada Ekspertów.",
  "/me/contributions": "Wstrzymaj lub wycofaj swój wkład w dowolnym momencie.",
  "/wiki": "Artykuły zatwierdzone przez Radę Ekspertów.",
  "/atlas": "Zdjęcia z oznaczonymi elementami, sprawdzone przez ekspertów.",
  "/chat": "Odpowiedzi wyłącznie na podstawie zatwierdzonej wiedzy, z cytowaniami.",
  "/review": "Szkice artykułów czekające na Twoją decyzję.",
  "/review/atlas": "Zdjęcia przekazane do atlasu czekające na weryfikację.",
  "/review/labels": "Etykiety dziedzin i propozycje członków.",
  "/admin/security": "Ryzyko kont, blokady i dziennik zdarzeń.",
  "/admin/llm": "Dostawcy, modele i przypisania funkcji.",
  "/admin/users": "Konta, role, hasła i blokady.",
  "/admin/atlas": "Dziedziny atlasu i eksport zbioru danych.",
};

/** The start page: the menu's sections as cards, for the account's roles (12.5). */
export function HomeDashboard() {
  const me = useMe();
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  const sections = visibleSections(me.data);
  const name = me.data?.display_name ?? me.data?.email;
  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-2">
        <h1 className="font-display text-3xl font-semibold">{name ? `Dzień dobry, ${name}` : "Spółdzielnia AI"}</h1>
        <p className="max-w-2xl text-text-muted">Wspólna, zweryfikowana wiedza pszczelarska – z poszanowaniem prywatności i praw autorów.</p>
        {signedOut && (
          <a
            href={loginUrl("/")}
            className="mt-2 inline-flex w-fit items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-strong pointer-coarse:min-h-11"
          >
            <LogIn aria-hidden className="size-4" />
            Zaloguj się
          </a>
        )}
      </section>
      {me.isPending && <Loading rows={2} />}
      {sections.map((section) => (
        <section key={section.title} aria-label={section.title} className="flex flex-col gap-3">
          <h2 className="text-xs font-semibold tracking-wide text-text-muted uppercase">{section.title}</h2>
          <ul className="grid gap-3 @2xl:grid-cols-2 @5xl:grid-cols-3">
            {section.items.map(({ href, label, icon: Icon }) => (
              <li key={href}>
                <Link href={href} className="block h-full">
                  <Card className="flex h-full gap-4 transition-colors hover:border-primary">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary-strong">
                      <Icon aria-hidden className="size-5" />
                    </span>
                    <span className="flex flex-col gap-1">
                      <span className="font-display text-lg font-semibold">{label}</span>
                      <span className="text-sm text-text-muted">{TEXT[href]}</span>
                    </span>
                  </Card>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
