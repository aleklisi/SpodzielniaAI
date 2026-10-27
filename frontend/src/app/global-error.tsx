"use client";

import "./globals.css";

import { LANG_COOKIE, parseLocale } from "@/i18n/locales";
import { readCookie } from "@/lib/api";
import { MESSAGES } from "@/messages";

// Replaces the root layout when it fails; Next's default uses inline styles (blocked by CSP).
// Without the layout there is no message provider: the language comes from the cookie (14.2).
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const locale = (typeof document !== "undefined" && parseLocale(readCookie(LANG_COOKIE, document.cookie))) || "pl";
  const text = MESSAGES[locale].common;
  return (
    <html lang={locale}>
      <body className="p-8">
        <h1 className="text-2xl font-semibold">{text.error.title}</h1>
        <button type="button" onClick={reset} className="mt-4 rounded-lg border px-4 py-2">
          {text.retry}
        </button>
      </body>
    </html>
  );
}
