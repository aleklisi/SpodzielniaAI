import type { Metadata } from "next";

import { LlmConsole } from "@/components/admin/llm/LlmConsole";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Modele AI · Spółdzielnia AI" };

export default function LlmPage() {
  return (
    <RequireRole role="ADMIN">
      <LlmConsole />
    </RequireRole>
  );
}
