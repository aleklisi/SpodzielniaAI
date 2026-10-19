"use client";

import Link from "next/link";

import { ArticleView } from "@/components/wiki/ArticleView";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { ApiError, RateLimited, SecurityLocked } from "@/lib/api";
import { useMe } from "@/lib/session";
import { REVIEW_ERRORS, ReviewRefused, useArticle, useSubmit } from "@/lib/wiki";

const SUBMITTABLE = new Set(["DRAFT", "REVISION_REQUIRED"]);

export function ArticlePage({ articleId }: { articleId: string }) {
  const article = useArticle(articleId);
  const me = useMe();
  const submit = useSubmit();
  const toast = useToast();

  if (article.isPending) return <p className="text-text-muted">Ładowanie artykułu…</p>;
  if (article.isError) {
    const error = article.error;
    const message =
      error instanceof RateLimited
        ? "Przeczytałeś dziś bardzo dużo artykułów. Spróbuj ponownie za jakiś czas."
        : error instanceof SecurityLocked
          ? "Twoje konto zostało zablokowane. Skontaktuj się z administratorem spółdzielni."
          : error instanceof ApiError && error.status === 404
            ? "Ten artykuł nie istnieje albo nie masz do niego dostępu."
            : "Nie udało się wczytać artykułu.";
    return (
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold">Artykuł niedostępny</h1>
        <p role="alert" className="text-text-muted">
          {message}
        </p>
        <Link href="/wiki" className="text-primary-strong underline">
          Wróć do bazy wiedzy
        </Link>
      </div>
    );
  }
  const data = article.data;
  const isAuthor = !!me.data && data.authors.some((a) => a.id === me.data.id);
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <Link href="/wiki" className="text-sm text-text-muted hover:text-text">
        ← Baza wiedzy
      </Link>
      <ArticleView article={data} />
      {isAuthor && SUBMITTABLE.has(data.status) && (
        <div>
          <Button
            busy={submit.isPending}
            onClick={async () => {
              try {
                await submit.mutateAsync(data.id);
                toast("Artykuł trafił do recenzji Rady Ekspertów.", "ok");
              } catch (error) {
                toast(error instanceof ReviewRefused ? (REVIEW_ERRORS[error.code] ?? error.code) : "Nie udało się wysłać.", "error");
              }
            }}
          >
            Wyślij do recenzji
          </Button>
        </div>
      )}
    </div>
  );
}
