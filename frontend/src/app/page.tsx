import Link from "next/link";

import { Card } from "@/components/ui/Card";

const SECTIONS = [
  { href: "/notebook", title: "Prywatny notatnik", text: "Twoje notatki, zdjęcia i nagrania. Widzisz je tylko Ty." },
  { href: "/wiki", title: "Baza wiedzy", text: "Artykuły zatwierdzone przez Radę Ekspertów." },
  { href: "/chat", title: "Zapytaj spółdzielnię", text: "Odpowiedzi wyłącznie na podstawie zatwierdzonej wiedzy, z cytowaniami." },
  { href: "/me/contributions", title: "Moje kontrybucje", text: "Wstrzymaj lub wycofaj swój wkład w dowolnym momencie." },
];

export default function Home() {
  return (
    <div className="flex flex-col gap-8">
      <section>
        <h1 className="font-display text-3xl font-semibold">Spółdzielnia AI</h1>
        <p className="mt-2 max-w-2xl text-text-muted">
          Wspólna, zweryfikowana wiedza pszczelarska – z poszanowaniem prywatności i praw autorów.
        </p>
      </section>
      <div className="grid gap-4 sm:grid-cols-2">
        {SECTIONS.map((s) => (
          <Link key={s.href} href={s.href} className="block">
            <Card className="h-full transition-colors hover:border-primary">
              <h2 className="font-display text-lg font-semibold">{s.title}</h2>
              <p className="mt-1 text-sm text-text-muted">{s.text}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
