import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import type { Messages } from "@/messages";

/** A page's `generateMetadata`: its title in the request's language (14.2). */
export function titled(key: keyof Messages["titles"]): () => Promise<Metadata> {
  return async () => ({ title: (await getTranslations("titles"))(key) });
}
