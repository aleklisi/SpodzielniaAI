import type { Metadata } from "next";

import { PhotoEditor } from "@/components/notebook/PhotoEditor";

export const metadata: Metadata = { title: "Zdjęcie · Spółdzielnia AI" };

export default async function PhotoPage(props: PageProps<"/notebook/[noteId]/photos/[attachmentId]">) {
  const { noteId, attachmentId } = await props.params;
  return <PhotoEditor noteId={noteId} attachmentId={attachmentId} />;
}
