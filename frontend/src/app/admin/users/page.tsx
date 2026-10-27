import { UsersConsole } from "@/components/admin/users/UsersConsole";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("users");

export default function UsersPage() {
  return (
    <RequireRole role="ADMIN">
      <UsersConsole />
    </RequireRole>
  );
}
