"use client";

/** Dataset exports of the atlas (9.7). ADMIN + MFA; off unless the operator enabled them. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_BASE, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type AtlasExport = components["schemas"]["ExportOut"];
export type ExportInput = components["schemas"]["ExportIn"];

const KEY = ["admin", "atlas", "exports"] as const;

export function useExports() {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => unwrap(await api.GET("/admin/atlas/exports")),
    refetchInterval: (query) => (query.state.data?.exports.some((e) => e.status === "PENDING") ? 3000 : false),
  });
}

export function useCreateExport() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: ExportInput) => unwrap(await api.POST("/admin/atlas/exports", { body })),
    onSettled: () => void client.invalidateQueries({ queryKey: KEY }),
  });
}

export function downloadUrl(id: string, token: string): string {
  return `${API_BASE}/admin/atlas/exports/${encodeURIComponent(id)}/download?token=${encodeURIComponent(token)}`;
}

export const EXPORT_STATUS: Record<AtlasExport["status"], string> = {
  PENDING: "Przygotowywany…",
  READY: "Gotowy do pobrania",
  FAILED: "Błąd",
  DOWNLOADED: "Pobrany",
  EXPIRED: "Wygasł",
};

const ERRORS: Record<string, string> = {
  export_disabled: "Eksport jest wyłączony w konfiguracji serwera.",
  rate_limited: "Dzienny limit eksportów został wyczerpany.",
  domain_not_found: "Nie znaleziono dziedziny.",
};

export function exportError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Nie udało się utworzyć eksportu.";
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  const code = detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string" ? detail.error : null;
  if (code && ERRORS[code]) return ERRORS[code];
  if (error.status === 429) return ERRORS.rate_limited;
  return "Nie udało się utworzyć eksportu.";
}
