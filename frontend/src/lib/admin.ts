"use client";

/** Admin security console (6.6): ADMIN + MFA on every call (the API enforces both). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type AuditEntry = components["schemas"]["AuditEntryOut"];
export type UserCoverage = components["schemas"]["UserCoverageOut"];
export type Coverage = components["schemas"]["CoverageOut"];
export type Chain = components["schemas"]["ChainOut"];

export interface AuditFilters {
  eventTypes: string[];
  actor: string;
  sinceDays: number | null;
}

export function sinceIso(days: number | null, now = Date.now()): string | undefined {
  return days === null ? undefined : new Date(now - days * 86_400_000).toISOString();
}

export function useCoverage() {
  return useQuery({
    queryKey: ["admin", "coverage"],
    queryFn: async () => unwrap(await api.GET("/admin/coverage", { params: { query: { limit: 500 } } })),
  });
}

export function useAlerts(sinceDays: number | null) {
  return useQuery({
    queryKey: ["admin", "alerts", sinceDays],
    queryFn: async () => unwrap(await api.GET("/admin/alerts", { params: { query: { since: sinceIso(sinceDays), limit: 200 } } })),
  });
}

export function useChain() {
  return useQuery({ queryKey: ["admin", "chain"], queryFn: async () => unwrap(await api.GET("/admin/audit-log/verify")) });
}

export function useAuditPage(filters: AuditFilters, cursor: number | null) {
  return useQuery({
    queryKey: ["admin", "audit", filters, cursor],
    placeholderData: (previous) => previous, // keep the frame while the next page loads
    queryFn: async () =>
      unwrap(
        await api.GET("/admin/audit-log", {
          params: {
            query: {
              event_type: filters.eventTypes.length ? filters.eventTypes : undefined,
              actor: /^[0-9a-f-]{36}$/i.test(filters.actor.trim()) ? filters.actor.trim() : undefined,
              since: sinceIso(filters.sinceDays),
              cursor: cursor ?? undefined,
              limit: 50,
            },
          },
        }),
      ),
  });
}

/** A user's shield decisions over time (the risk score each recorded). */
export function useRiskHistory(userId: string | null) {
  return useQuery({
    queryKey: ["admin", "risk", userId],
    enabled: userId !== null,
    queryFn: async () =>
      unwrap(
        await api.GET("/admin/audit-log", {
          params: { query: { actor: userId ?? undefined, event_type: ["SHIELD_DECISION"], limit: 200 } },
        }),
      ),
  });
}

export function useUnlock() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string; reason: string; resetRisk: boolean }) =>
      unwrap(
        await api.POST("/admin/users/{user_id}/unlock", {
          params: { path: { user_id: input.userId } },
          body: { reason: input.reason, reset_risk: input.resetRisk },
        }),
      ),
    onSettled: () => void client.invalidateQueries({ queryKey: ["admin"] }),
  });
}

export const LEVELS = {
  NORMAL: { label: "Normalny", icon: "●", color: "var(--status-good)" },
  ELEVATED: { label: "Podwyższony", icon: "▲", color: "var(--status-warning)" },
  HIGH: { label: "Wysoki", icon: "◆", color: "var(--status-serious)" },
  CRITICAL: { label: "Krytyczny", icon: "■", color: "var(--status-critical)" },
} as const;

export type Level = keyof typeof LEVELS;

/** Accounts by how close they are to a coverage limit (the closest window), in buckets. */
export const BUCKETS = [
  { label: "< 25%", max: 0.25 },
  { label: "25–50%", max: 0.5 },
  { label: "50–75%", max: 0.75 },
  { label: "75–100%", max: 1 },
  { label: "≥ 100%", max: Infinity },
] as const;

export function histogram(users: Array<Pick<UserCoverage, "fraction_of_limit">>): number[] {
  const counts = BUCKETS.map(() => 0);
  for (const u of users) {
    const index = BUCKETS.findIndex((b) => u.fraction_of_limit < b.max);
    counts[index === -1 ? BUCKETS.length - 1 : index] += 1;
  }
  return counts;
}

export const EVENT_LABELS: Record<string, string> = {
  HONEYPOT_TRIGGERED: "Pobrano kanarka",
  COVERAGE_LOCK: "Przekroczony budżet pokrycia",
  VERBATIM_BLOCKED: "Zablokowana odpowiedź dosłowna",
  REFRESH_TOKEN_REUSE: "Ponowne użycie tokenu odświeżania",
  SHIELD_DECISION: "Decyzja tarczy",
  ACCOUNT_UNLOCKED: "Odblokowano konto",
  SECURITY_DASHBOARD_ACCESSED: "Wgląd w panel bezpieczeństwa",
  CONSENT_REVOKED: "Wycofanie zgody",
  CONSENT_SUSPENDED: "Wstrzymanie zgody",
  CONSENT_RESUMED: "Wznowienie zgody",
  ARTICLE_STATUS_CHANGED: "Zmiana statusu artykułu",
  USER_LOGIN: "Logowanie",
};
