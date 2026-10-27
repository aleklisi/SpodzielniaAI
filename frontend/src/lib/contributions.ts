"use client";

/** Creator's Exit Strategy (6.4, backend 4.3): suspend / resume / revoke own contributions. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { useTranslations } from "next-intl";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";
import { wikiKeys } from "@/lib/wiki";

export type ConsentResult = components["schemas"]["ConsentOut"];
export type HistoryEntry = components["schemas"]["HistoryEntryOut"];
export type ConsentAction = "suspend" | "resume" | "revoke";

const historyKey = ["me", "contributions", "history"] as const;

export function useHistory() {
  return useQuery({ queryKey: historyKey, queryFn: async () => unwrap(await api.GET("/me/contributions/history")) });
}

/** No optimistic update: the panel shows the server's answer, for revoke above all. */
export function useConsent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ action, articleIds }: { action: ConsentAction; articleIds: string[] | null }) => {
      const scope = articleIds ? { article_ids: articleIds } : {};
      if (action === "revoke") {
        return unwrap(await api.POST("/me/contributions/revoke", { body: { ...scope, confirm: "REVOKE" } }));
      }
      const path = action === "suspend" ? "/me/contributions/suspend" : "/me/contributions/resume";
      return unwrap(await api.POST(path, { body: scope }));
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: wikiKeys.contributions });
      void client.invalidateQueries({ queryKey: historyKey });
      void client.invalidateQueries({ queryKey: wikiKeys.articles });
    },
  });
}

type Translate = ReturnType<typeof useTranslations<"contributions">>;

/** The toast after a consent change, in the current language (`t` of `contributions`). */
export function describeResult(t: Translate, action: ConsentAction, result: ConsentResult): string {
  const parts = [t(`result.${action}`, { count: result.blocks_affected })];
  if (result.degraded.length) parts.push(t("result.degraded", { count: result.degraded.length }));
  if (result.withdrawn.length) parts.push(t("result.withdrawn", { count: result.withdrawn.length }));
  if (!result.index_synced) parts.push(t("result.syncing"));
  return parts.join(" ");
}
