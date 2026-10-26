import type { Metadata } from "next";

import { NoteWorkspace } from "@/components/notebook/NoteWorkspace";

export const metadata: Metadata = { title: "Notatka" };

export default async function NotePage(props: PageProps<"/notebook/[noteId]">) {
  const { noteId } = await props.params;
  return <NoteWorkspace noteId={noteId} />;
}
