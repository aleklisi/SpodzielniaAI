import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";
import { headers } from "next/headers";
import Link from "next/link";

import { ToastProvider } from "@/components/ui/Toast";
import { NonceProvider } from "@/lib/nonce";
import { QueryProvider } from "@/lib/query";

import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin", "latin-ext"] });
const outfit = Outfit({ variable: "--font-outfit", subsets: ["latin", "latin-ext"] });

export const metadata: Metadata = {
  title: "Spółdzielnia AI",
  description: "Wspólna baza wiedzy spółdzielni pszczelarskiej",
};

const NAV = [
  { href: "/notebook", label: "Notatnik" },
  { href: "/wiki", label: "Wiki" },
  { href: "/chat", label: "Zapytaj spółdzielnię" },
  { href: "/me/contributions", label: "Moje kontrybucje" },
  { href: "/review", label: "Recenzje" },
  { href: "/admin/security", label: "Bezpieczeństwo" },
];

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Reading the request headers makes every page render per request: the CSP nonce from
  // src/proxy.ts differs on each one (Next.js applies it to its scripts automatically).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="pl" className={`${inter.variable} ${outfit.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col font-sans">
        <NonceProvider nonce={nonce}>
        <QueryProvider>
        <ToastProvider>
          <header className="border-b border-border bg-surface">
            <nav aria-label="Główna" className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
              <Link href="/" className="font-display text-lg font-semibold text-primary-strong">
                Spółdzielnia AI
              </Link>
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="text-sm text-text-muted hover:text-text">
                  {item.label}
                </Link>
              ))}
            </nav>
          </header>
          <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
        </ToastProvider>
        </QueryProvider>
        </NonceProvider>
      </body>
    </html>
  );
}
