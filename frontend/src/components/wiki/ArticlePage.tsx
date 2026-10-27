"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { ArticleView } from "@/components/wiki/ArticleView";
import { Button } from "@/components/ui/Button";
import { Loading } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { ApiError, RateLimited, SecurityLocked } from "@/lib/api";
import { useMe } from "@/lib/session";
import { reviewErrorKey, ReviewRefused, useArticle, useSubmit } from "@/lib/wiki";

const SUBMITTABLE = new Set(["DRAFT", "REVISION_REQUIRED"]);

export function ArticlePage({ articleId }: { articleId: string }) {
  const t = useTranslations("wiki");
  const article = useArticle(articleId);
  const me = useMe();
  const submit = useSubmit();
  const toast = useToast();

  if (article.isPending) return <Loading label={t("page.loading")} />;
  if (article.isError) {
    const error = article.error;
    const message =
      error instanceof RateLimited
        ? t("page.rateLimited")
        : error instanceof SecurityLocked
          ? t("page.locked")
          : error instanceof ApiError && error.status === 404
            ? t("page.notFound")
            : t("page.loadFailed");
    return (
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold">{t("page.unavailable")}</h1>
        <p role="alert" className="text-text-muted">
          {message}
        </p>
        <Link href="/wiki" className="text-primary-strong underline">
          {t("page.back")}
        </Link>
      </div>
    );
  }
  const data = article.data;
  const isAuthor = !!me.data && data.authors.some((a) => a.id === me.data.id);
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <Link href="/wiki" className="text-sm text-text-muted hover:text-text">
        {t("page.backShort")}
      </Link>
      <ArticleView article={data} />
      {isAuthor && SUBMITTABLE.has(data.status) && (
        <div>
          <Button
            busy={submit.isPending}
            onClick={async () => {
              try {
                await submit.mutateAsync(data.id);
                toast(t("page.submitted"), "ok");
              } catch (error) {
                const key = error instanceof ReviewRefused ? reviewErrorKey(error.code) : null;
                toast(key ? t(`reviewErrors.${key}`) : error instanceof ReviewRefused ? error.code : t("page.submitFailed"), "error");
              }
            }}
          >
            {t("page.submit")}
          </Button>
        </div>
      )}
    </div>
  );
}
