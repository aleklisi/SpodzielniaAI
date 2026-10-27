import { AtlasContributions } from "@/components/contributions/AtlasContributions";
import { ContributionsPanel } from "@/components/contributions/ContributionsPanel";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("contributions");

export default function ContributionsPage() {
  return (
    <div className="flex flex-col gap-10">
      <ContributionsPanel />
      <AtlasContributions />
    </div>
  );
}
