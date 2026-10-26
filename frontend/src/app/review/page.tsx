import type { Metadata } from "next";

import { RequireRole } from "@/components/auth/RequireRole";
import { ReviewPanel } from "@/components/review/ReviewPanel";

export const metadata: Metadata = { title: "Recenzje" };

export default function ReviewPage() {
  return (
    <RequireRole role="EXPERT">
      <ReviewPanel />
    </RequireRole>
  );
}
