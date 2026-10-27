import { LlmConsole } from "@/components/admin/llm/LlmConsole";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("llm");

export default function LlmPage() {
  return (
    <RequireRole role="ADMIN">
      <LlmConsole />
    </RequireRole>
  );
}
