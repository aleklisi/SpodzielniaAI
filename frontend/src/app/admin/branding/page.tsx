import type { Metadata } from "next";

import { BrandingConsole } from "@/components/admin/branding/BrandingConsole";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Wygląd" };

export default function BrandingPage() {
  return (
    <RequireRole role="ADMIN">
      <BrandingConsole />
    </RequireRole>
  );
}
