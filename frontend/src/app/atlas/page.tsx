import type { Metadata } from "next";

import { AtlasGallery } from "@/components/atlas/AtlasGallery";

export const metadata: Metadata = { title: "Atlas" };

export default function AtlasPage() {
  return <AtlasGallery />;
}
