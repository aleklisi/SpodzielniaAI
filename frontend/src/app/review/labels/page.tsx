import type { Metadata } from "next";

import { LabelVocabulary } from "@/components/atlas/LabelVocabulary";
import { RequireRole } from "@/components/auth/RequireRole";

export const metadata: Metadata = { title: "Słownik etykiet" };

export default function LabelsPage() {
  return (
    <RequireRole role="EXPERT">
      <LabelVocabulary />
    </RequireRole>
  );
}
