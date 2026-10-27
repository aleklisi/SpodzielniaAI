import { RequireRole } from "@/components/auth/RequireRole";
import { AtlasReviewPanel } from "@/components/review/AtlasReviewPanel";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("atlasReview");

export default function AtlasReviewPage() {
  return (
    <RequireRole role="EXPERT">
      <AtlasReviewPanel />
    </RequireRole>
  );
}
