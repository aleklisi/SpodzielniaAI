import Link from "next/link";

// Next's built-in 404 page uses inline style attributes, which the CSP (style-src nonce) blocks.
export default function NotFound() {
  return (
    <div className="flex flex-col items-start gap-3">
      <h1 className="font-display text-2xl font-semibold">Nie znaleziono strony</h1>
      <p className="text-text-muted">Ta strona nie istnieje albo nie masz do niej dostępu.</p>
      <Link href="/" className="text-primary-strong underline">
        Wróć na stronę główną
      </Link>
    </div>
  );
}
