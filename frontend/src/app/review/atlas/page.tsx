import type { Metadata } from "next";

import { RequireRole } from "@/components/auth/RequireRole";
import { AtlasReviewPanel } from "@/components/review/AtlasReviewPanel";

export const metadata: Metadata = { title: "Atlas: recenzje" };

export default function AtlasReviewPage() {
  return (
    <RequireRole role="EXPERT">
      <AtlasReviewPanel />
    </RequireRole>
  );
}
