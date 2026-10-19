import type { Metadata } from "next";

import { ContributionsPanel } from "@/components/contributions/ContributionsPanel";

export const metadata: Metadata = { title: "Moje kontrybucje · Spółdzielnia AI" };

export default function ContributionsPage() {
  return <ContributionsPanel />;
}
