import { ProcessorsPage } from "@/components/privacy/ProcessorsPage";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("privacy");

export default function PrivacyPage() {
  return <ProcessorsPage />;
}
