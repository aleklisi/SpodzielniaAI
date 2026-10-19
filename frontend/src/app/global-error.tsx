"use client";

import "./globals.css";

// Replaces the root layout when it fails; Next's default uses inline styles (blocked by CSP).
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="pl">
      <body className="p-8">
        <h1 className="text-2xl font-semibold">Coś poszło nie tak</h1>
        <button type="button" onClick={reset} className="mt-4 rounded-lg border px-4 py-2">
          Spróbuj ponownie
        </button>
      </body>
    </html>
  );
}
