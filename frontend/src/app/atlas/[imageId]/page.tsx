import { AtlasPhotoPage } from "@/components/atlas/AtlasPhotoPage";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("atlasPhoto");

export default async function AtlasImagePage(props: PageProps<"/atlas/[imageId]">) {
  const { imageId } = await props.params;
  return <AtlasPhotoPage imageId={imageId} />;
}
