import { Drafts } from "@/components/wiki/Drafts";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("drafts");

export default function DraftsPage() {
  return <Drafts />;
}
