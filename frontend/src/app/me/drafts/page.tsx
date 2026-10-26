import type { Metadata } from "next";

import { Drafts } from "@/components/wiki/Drafts";

export const metadata: Metadata = { title: "Moje szkice" };

export default function DraftsPage() {
  return <Drafts />;
}
