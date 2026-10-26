import type { Metadata } from "next";

import { AtlasContributions } from "@/components/contributions/AtlasContributions";
import { ContributionsPanel } from "@/components/contributions/ContributionsPanel";

export const metadata: Metadata = { title: "Moje kontrybucje" };

export default function ContributionsPage() {
  return (
    <div className="flex flex-col gap-10">
      <ContributionsPanel />
      <AtlasContributions />
    </div>
  );
}
