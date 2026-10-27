import { AtlasGallery } from "@/components/atlas/AtlasGallery";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("atlas");

export default function AtlasPage() {
  return <AtlasGallery />;
}
