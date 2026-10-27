import { getTranslations } from "next-intl/server";

import { DomainsPanel } from "@/components/admin/atlas/DomainsPanel";
import { ExportPanel } from "@/components/admin/atlas/ExportPanel";
import { RequireRole } from "@/components/auth/RequireRole";
import { PageHeader } from "@/components/ui/PageHeader";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("adminAtlas");

export default async function AdminAtlasPage() {
  const t = await getTranslations("adminAtlas");
  return (
    <RequireRole role="ADMIN">
      <div className="flex flex-col gap-8">
        <PageHeader title={t("title")} description={t("description")} />
        <DomainsPanel />
        <ExportPanel />
      </div>
    </RequireRole>
  );
}
