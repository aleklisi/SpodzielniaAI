import type { Metadata, Viewport } from "next";
import { Inter, Outfit } from "next/font/google";
import { cookies, headers } from "next/headers";

import { ProcessorsBanner } from "@/components/privacy/ProcessorsBanner";
import { AppShell } from "@/components/shell/AppShell";
import { ToastProvider } from "@/components/ui/Toast";
import { NonceProvider } from "@/lib/nonce";
import { QueryProvider } from "@/lib/query";
import { isCollapsed, SIDEBAR_COOKIE } from "@/lib/sidebar";

import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin", "latin-ext"] });
const outfit = Outfit({ variable: "--font-outfit", subsets: ["latin", "latin-ext"] });

export const metadata: Metadata = {
  title: "Spółdzielnia AI",
  description: "Wspólna baza wiedzy spółdzielni pszczelarskiej",
};

// `cover` lets the shell pad for notches and the home indicator (env(safe-area-inset-*)).
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Reading the request headers makes every page render per request: the CSP nonce from
  // src/proxy.ts differs on each one (Next.js applies it to its scripts automatically).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  const collapsed = isCollapsed((await cookies()).get(SIDEBAR_COOKIE)?.value);
  return (
    <html lang="pl" className={`${inter.variable} ${outfit.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <a
          href="#content"
          className="sr-only z-50 rounded-lg bg-surface px-4 py-2 font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          Przejdź do treści
        </a>
        <NonceProvider nonce={nonce}>
          <QueryProvider>
            <ToastProvider>
              <AppShell initialCollapsed={collapsed} banner={<ProcessorsBanner />}>
                {children}
              </AppShell>
            </ToastProvider>
          </QueryProvider>
        </NonceProvider>
      </body>
    </html>
  );
}
