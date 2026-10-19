import type { Metadata } from "next";

import { ArticlePage } from "@/components/wiki/ArticlePage";

export const metadata: Metadata = { title: "Artykuł · Spółdzielnia AI" };

export default async function WikiArticlePage(props: PageProps<"/wiki/[articleId]">) {
  const { articleId } = await props.params;
  return <ArticlePage articleId={articleId} />;
}
