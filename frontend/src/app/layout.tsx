import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";
import { headers } from "next/headers";

import { MainNav } from "@/components/nav/MainNav";
import { ProcessorsBanner } from "@/components/privacy/ProcessorsBanner";
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
                <MainNav />
              </header>
              <ProcessorsBanner />
              <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
            </ToastProvider>
          </QueryProvider>
        </NonceProvider>
      </body>
    </html>
  );
}
