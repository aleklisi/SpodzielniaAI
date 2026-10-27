import { LabelVocabulary } from "@/components/atlas/LabelVocabulary";
import { RequireRole } from "@/components/auth/RequireRole";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("labels");

export default function LabelsPage() {
  return (
    <RequireRole role="EXPERT">
      <LabelVocabulary />
    </RequireRole>
  );
}
