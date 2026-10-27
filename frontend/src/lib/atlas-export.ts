"use client";

/** Dataset exports of the atlas (9.7). ADMIN + MFA; off unless the operator enabled them. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_BASE, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { apiErrorCode, knownCode } from "@/lib/messages";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

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

export type ExportErrorKey = keyof Messages["adminAtlas"]["export"]["errors"];

const CODES = ["export_disabled", "rate_limited", "domain_not_found"] as const satisfies readonly ExportErrorKey[];

/** The key in `adminAtlas.export.errors`. Statuses are in `adminAtlas.export.status`. */
export function exportErrorKey(error: unknown): ExportErrorKey {
  const code = knownCode(CODES, apiErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 429) return "rate_limited";
  return "failed";
}
