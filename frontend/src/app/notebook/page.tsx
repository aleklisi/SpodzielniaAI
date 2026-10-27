import { NotebookList } from "@/components/notebook/NotebookList";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("notebook");

export default function NotebookPage() {
  return <NotebookList />;
}
