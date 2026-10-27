import { WikiList } from "@/components/wiki/WikiList";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("wiki");

export default function WikiPage() {
  return <WikiList />;
}
