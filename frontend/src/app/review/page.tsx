import { RequireRole } from "@/components/auth/RequireRole";
import { ReviewPanel } from "@/components/review/ReviewPanel";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("review");

export default function ReviewPage() {
  return (
    <RequireRole role="EXPERT">
      <ReviewPanel />
    </RequireRole>
  );
}
