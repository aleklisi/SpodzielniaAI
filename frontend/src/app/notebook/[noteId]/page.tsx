import { NoteWorkspace } from "@/components/notebook/NoteWorkspace";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("note");

export default async function NotePage(props: PageProps<"/notebook/[noteId]">) {
  const { noteId } = await props.params;
  return <NoteWorkspace noteId={noteId} />;
}
