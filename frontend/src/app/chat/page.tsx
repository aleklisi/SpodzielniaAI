import { ChatView } from "@/components/chat/ChatView";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("chat");

export default function ChatPage() {
  return <ChatView />;
}
