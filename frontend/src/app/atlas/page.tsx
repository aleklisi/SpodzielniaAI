import type { Metadata } from "next";

import { AtlasGallery } from "@/components/atlas/AtlasGallery";

export const metadata: Metadata = { title: "Atlas · Spółdzielnia AI" };

export default function AtlasPage() {
  return <AtlasGallery />;
}
