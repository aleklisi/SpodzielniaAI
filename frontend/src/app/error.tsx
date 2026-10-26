"use client";

import { TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="font-display text-2xl font-semibold">Coś poszło nie tak</h1>
      <EmptyState icon={TriangleAlert} title="Spróbuj ponownie za chwilę." action={<Button onClick={reset}>Spróbuj ponownie</Button>} />
    </div>
  );
}
