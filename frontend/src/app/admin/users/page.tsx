import type { Metadata } from "next";

import { UsersConsole } from "@/components/admin/users/UsersConsole";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Użytkownicy · Spółdzielnia AI" };

export default function UsersPage() {
  return (
    <RequireRole role="ADMIN">
      <UsersConsole />
    </RequireRole>
  );
}
