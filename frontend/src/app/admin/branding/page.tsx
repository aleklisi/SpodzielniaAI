import { BrandingConsole } from "@/components/admin/branding/BrandingConsole";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("branding");

export default function BrandingPage() {
  return (
    <RequireRole role="ADMIN">
      <BrandingConsole />
    </RequireRole>
  );
}
