"use client";

import { TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useTranslations("common");
  return (
    <div className="flex flex-col gap-4">
      <h1 className="font-display text-2xl font-semibold">{t("error.title")}</h1>
      <EmptyState icon={TriangleAlert} title={t("error.body")} action={<Button onClick={reset}>{t("retry")}</Button>} />
    </div>
  );
}
