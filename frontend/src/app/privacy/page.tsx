import type { Metadata } from "next";

import { ProcessorsPage } from "@/components/privacy/ProcessorsPage";

export const metadata: Metadata = { title: "Gdzie przetwarzane są Twoje dane" };

export default function PrivacyPage() {
  return <ProcessorsPage />;
}
