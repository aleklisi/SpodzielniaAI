import type { Metadata } from "next";

import { NotebookList } from "@/components/notebook/NotebookList";

export const metadata: Metadata = { title: "Notatnik · Spółdzielnia AI" };

export default function NotebookPage() {
  return <NotebookList />;
}
