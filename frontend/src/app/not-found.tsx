import { SearchX } from "lucide-react";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { EmptyState } from "@/components/ui/EmptyState";

// Next's built-in 404 page uses inline style attributes, which the CSP (style-src nonce) blocks.
export default async function NotFound() {
  const t = await getTranslations("common.notFound");
  return (
    <div className="flex flex-col gap-4">
      <h1 className="font-display text-2xl font-semibold">{t("title")}</h1>
      <EmptyState
        icon={SearchX}
        title={t("body")}
        action={
          <Link href="/" className="font-medium text-primary-strong underline">
            {t("home")}
          </Link>
        }
      />
    </div>
  );
}
