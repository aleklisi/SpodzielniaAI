import { SecurityDashboard } from "@/components/admin/SecurityDashboard";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("security");

export default function SecurityPage() {
  return (
    <RequireRole role="ADMIN">
      <SecurityDashboard />
    </RequireRole>
  );
}
