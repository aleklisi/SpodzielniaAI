import type { Metadata } from "next";

import { DomainsPanel } from "@/components/admin/atlas/DomainsPanel";
import { ExportPanel } from "@/components/admin/atlas/ExportPanel";
import { RequireRole } from "@/components/auth/RequireRole";
import { PageHeader } from "@/components/ui/PageHeader";

export const metadata: Metadata = { title: "Atlas" };

export default function AdminAtlasPage() {
  return (
    <RequireRole role="ADMIN">
      <div className="flex flex-col gap-8">
        <PageHeader title="Atlas: administracja" description="Dziedziny atlasu, ich słowniki i eksport zweryfikowanego zbioru danych." />
        <DomainsPanel />
        <ExportPanel />
      </div>
    </RequireRole>
  );
}
