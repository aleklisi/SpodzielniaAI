import type { Metadata } from "next";

import { DomainsPanel } from "@/components/admin/atlas/DomainsPanel";
import { ExportPanel } from "@/components/admin/atlas/ExportPanel";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Atlas · Spółdzielnia AI" };

export default function AdminAtlasPage() {
  return (
    <RequireRole role="ADMIN">
      <div className="flex flex-col gap-8">
        <h1 className="font-display text-2xl font-semibold">Atlas: administracja</h1>
        <DomainsPanel />
        <ExportPanel />
      </div>
    </RequireRole>
  );
}
