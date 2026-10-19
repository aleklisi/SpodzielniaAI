"use client";

/** Creator's Exit Strategy (6.4, backend 4.3): suspend / resume / revoke own contributions. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

const VERB: Record<ConsentAction, string> = { suspend: "Wstrzymano", resume: "Wznowiono", revoke: "Usunięto trwale" };

/** Polish plural: 1 → one, 2–4 (not 12–14) → few, otherwise many. */
export function plural(n: number, one: string, few: string, many: string): string {
  if (n === 1) return one;
  const tens = n % 100;
  return n % 10 >= 2 && n % 10 <= 4 && (tens < 12 || tens > 14) ? few : many;
}

export function blocksWord(n: number): string {
  return plural(n, "blok", "bloki", "bloków");
}

export function describeResult(action: ConsentAction, result: ConsentResult): string {
  const parts = [`${VERB[action]} ${result.blocks_affected} ${blocksWord(result.blocks_affected)}.`];
  const degraded = result.degraded.length;
  if (degraded) {
    parts.push(
      `Do recenzji ${plural(degraded, "wrócił", "wróciły", "wróciło")} ${degraded} ` +
        `${plural(degraded, "współtworzony artykuł", "współtworzone artykuły", "współtworzonych artykułów")}.`,
    );
  }
  const withdrawn = result.withdrawn.length;
  if (withdrawn) parts.push(`Wycofano z bazy wiedzy ${withdrawn} ${plural(withdrawn, "artykuł", "artykuły", "artykułów")}.`);
  if (!result.index_synced) parts.push("Wyszukiwarka zaktualizuje się za chwilę.");
  return parts.join(" ");
}
