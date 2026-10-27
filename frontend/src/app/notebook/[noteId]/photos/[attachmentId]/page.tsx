import { PhotoEditor } from "@/components/notebook/PhotoEditor";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("photo");

export default async function PhotoPage(props: PageProps<"/notebook/[noteId]/photos/[attachmentId]">) {
  const { noteId, attachmentId } = await props.params;
  return <PhotoEditor noteId={noteId} attachmentId={attachmentId} />;
}
