import type { Metadata } from "next";

import { AtlasPhotoPage } from "@/components/atlas/AtlasPhotoPage";

export const metadata: Metadata = { title: "Zdjęcie atlasu" };

export default async function AtlasImagePage(props: PageProps<"/atlas/[imageId]">) {
  const { imageId } = await props.params;
  return <AtlasPhotoPage imageId={imageId} />;
}
