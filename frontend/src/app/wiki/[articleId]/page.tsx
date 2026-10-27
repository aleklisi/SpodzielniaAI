import { ArticlePage } from "@/components/wiki/ArticlePage";
import { titled } from "@/i18n/metadata";

export const generateMetadata = titled("article");

export default async function WikiArticlePage(props: PageProps<"/wiki/[articleId]">) {
  const { articleId } = await props.params;
  return <ArticlePage articleId={articleId} />;
}
