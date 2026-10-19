"use client";

import { Button } from "@/components/ui/Button";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3">
      <h1 className="font-display text-2xl font-semibold">Coś poszło nie tak</h1>
      <p className="text-text-muted">Spróbuj ponownie za chwilę.</p>
      <Button onClick={reset}>Spróbuj ponownie</Button>
    </div>
  );
}
