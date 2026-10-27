"use client";

import { useTranslations } from "next-intl";

/** A feature that is off until an admin configures a model for it (10.2). */
export function ModelMissing({ what }: { what: string }) {
  const t = useTranslations("common.modelMissing");
  return (
    <p role="status" className="rounded-lg border border-warn bg-surface-muted px-3 py-2 text-sm">
      <strong>{t("title")}.</strong> {t("body", { what })}
    </p>
  );
}
