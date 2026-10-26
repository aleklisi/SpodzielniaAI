import type { Metadata } from "next";

import { NotebookList } from "@/components/notebook/NotebookList";

export const metadata: Metadata = { title: "Notatnik" };

export default function NotebookPage() {
  return <NotebookList />;
}
