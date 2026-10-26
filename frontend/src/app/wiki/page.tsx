import type { Metadata } from "next";

import { WikiList } from "@/components/wiki/WikiList";

export const metadata: Metadata = { title: "Baza wiedzy" };

export default function WikiPage() {
  return <WikiList />;
}
