import type { Metadata } from "next";

import { SecurityDashboard } from "@/components/admin/SecurityDashboard";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Bezpieczeństwo · Spółdzielnia AI" };

export default function SecurityPage() {
  return (
    <RequireRole role="ADMIN">
      <SecurityDashboard />
    </RequireRole>
  );
}
