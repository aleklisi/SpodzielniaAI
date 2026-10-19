import type { Metadata } from "next";

import { ChatView } from "@/components/chat/ChatView";

export const metadata: Metadata = { title: "Zapytaj spółdzielnię · Spółdzielnia AI" };

export default function ChatPage() {
  return <ChatView />;
}
