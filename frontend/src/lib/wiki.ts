"use client";

/** Wiki, review queue and drafts (6.3). Visibility is the backend's: members see canonical
 * articles, authors their own drafts, experts with MFA the review queue. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";
import { knownCode } from "@/lib/messages";
import type { Messages } from "@/messages";

export type ArticleSummary = components["schemas"]["ArticleSummaryOut"];
export type Article = components["schemas"]["ArticleOut"];
export type Block = components["schemas"]["BlockOut"];
export type QueueItem = components["schemas"]["QueueItemOut"];
export type Decision = components["schemas"]["ReviewDecision"];
export type Contribution = components["schemas"]["ContributionOut"];

export const wikiKeys = {
  articles: ["wiki", "articles"] as const,
  article: (id: string) => ["wiki", "articles", id] as const,
  queue: ["reviews", "queue"] as const,
  contributions: ["me", "contributions"] as const,
};

export function useArticles() {
  return useQuery({
    queryKey: wikiKeys.articles,
    queryFn: async () => unwrap(await api.GET("/wiki/articles", { params: { query: { limit: 100 } } })),
  });
}

export function useArticle(articleId: string | null) {
  return useQuery({
    queryKey: wikiKeys.article(articleId ?? ""),
    enabled: articleId !== null,
    queryFn: async () =>
      unwrap(await api.GET("/wiki/articles/{article_id}", { params: { path: { article_id: articleId ?? "" } } })),
  });
}

export function useReviewQueue(enabled: boolean) {
  return useQuery({
    queryKey: wikiKeys.queue,
    enabled,
    queryFn: async () => unwrap(await api.GET("/reviews/queue")),
  });
}

export function useContributions() {
  return useQuery({ queryKey: wikiKeys.contributions, queryFn: async () => unwrap(await api.GET("/me/contributions")) });
}

/** A review/submit refusal the form can explain (`{"detail": {"error": code}}`). */
export class ReviewRefused extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function refusal(error: unknown): string | null {
  const detail = (error as { detail?: { error?: unknown } } | undefined)?.detail;
  return typeof detail?.error === "string" ? detail.error : null;
}

export interface DecisionInput {
  articleId: string;
  decision: Decision;
  version: number;
  comment: string;
  supersedesId: string | null;
}

export function useDecide() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: DecisionInput) => {
      const result = await api.POST("/reviews/{article_id}", {
        params: { path: { article_id: input.articleId } },
        body: {
          decision: input.decision,
          version: input.version,
          comment: input.comment.trim() || null,
          supersedes_id: input.supersedesId,
        },
      });
      const code = result.data ? null : refusal(result.error);
      if (code) throw new ReviewRefused(code);
      return unwrap(result);
    },
    onSettled: (_data, _error, input) => {
      void client.invalidateQueries({ queryKey: wikiKeys.queue });
      void client.invalidateQueries({ queryKey: wikiKeys.article(input.articleId) });
      void client.invalidateQueries({ queryKey: wikiKeys.articles });
    },
  });
}

export function useSubmit() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (articleId: string) => {
      const result = await api.POST("/wiki/articles/{article_id}/submit", {
        params: { path: { article_id: articleId } },
      });
      const code = result.data ? null : refusal(result.error);
      if (code) throw new ReviewRefused(code);
      return unwrap(result);
    },
    onSettled: (_data, _error, articleId) => {
      void client.invalidateQueries({ queryKey: wikiKeys.article(articleId) });
      void client.invalidateQueries({ queryKey: wikiKeys.contributions });
    },
  });
}

export type ReviewErrorKey = keyof Messages["wiki"]["reviewErrors"];

const REVIEW_CODES = [
  "version_conflict",
  "invalid_transition",
  "own_article",
  "no_content",
  "supersedes_not_found",
  "supersedes_not_canonical",
  "supersedes_self",
  "supersedes_requires_approve",
  "not_found",
] as const satisfies readonly ReviewErrorKey[];

/** The key in `wiki.reviewErrors` for a refusal code, if it is a known one. */
export function reviewErrorKey(code: string): ReviewErrorKey | null {
  return knownCode(REVIEW_CODES, code);
}

/** Plain text of an article's visible blocks (for the supersede diff). */
export function articleText(article: Article | undefined): string {
  return (article?.blocks ?? [])
    .map((b) => b.content ?? "")
    .filter(Boolean)
    .join("\n\n");
}
