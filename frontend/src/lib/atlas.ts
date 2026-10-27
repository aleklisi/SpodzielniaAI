"use client";

/**
 * Visual atlas (Stage 9). This file: domains and the label vocabulary (9.1).
 * Members read and propose; experts curate (`/atlas/curation`, EXPERT/ADMIN + MFA); admins
 * manage domains (`/admin/atlas`, ADMIN + MFA). The API enforces every rule.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";
import { apiErrorCode, knownCode } from "@/lib/messages";
import type { Messages } from "@/messages";

export type Domain = components["schemas"]["DomainOut"];
export type Label = components["schemas"]["LabelOut"];
export type CuratedLabel = components["schemas"]["CuratedLabelOut"];
export type LabelStatus = Label["status"];
export type DomainStatus = Domain["status"];

/** Labels in `atlas.labelStatus`. */
export const LABEL_STATUS_TONE: Record<LabelStatus, "ok" | "warn" | "neutral" | "danger"> = {
  ACTIVE: "ok",
  PROPOSED: "warn",
  DEPRECATED: "neutral",
  REJECTED: "danger",
};

const KEY = ["atlas"] as const;

export function useDomains() {
  return useQuery({
    queryKey: [...KEY, "domains"],
    queryFn: async () => unwrap(await api.GET("/atlas/domains")),
  });
}

export function useLabels(domainId: string | null, q: string) {
  return useQuery({
    queryKey: [...KEY, "labels", domainId, q],
    enabled: domainId !== null,
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(await api.GET("/atlas/labels", { params: { query: { domain_id: domainId ?? undefined, q: q || undefined } } })),
  });
}

export function useCuratedLabels(domainId: string | null, q: string, status: LabelStatus | "") {
  return useQuery({
    queryKey: [...KEY, "curation", domainId, q, status],
    enabled: domainId !== null,
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(
        await api.GET("/atlas/curation/labels", {
          params: { query: { domain_id: domainId ?? undefined, q: q || undefined, status: status ? [status] : undefined } },
        }),
      ),
  });
}

/** Every atlas write refreshes the atlas queries. */
function useAtlasMutation<I, O>(fn: (input: I) => Promise<O>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => void client.invalidateQueries({ queryKey: KEY }) });
}

export function useProposeLabel() {
  return useAtlasMutation(async (body: components["schemas"]["ProposalIn"]) =>
    unwrap(await api.POST("/atlas/labels/proposals", { body })),
  );
}

export function useCreateLabel() {
  return useAtlasMutation(async (body: components["schemas"]["LabelIn"]) => unwrap(await api.POST("/atlas/curation/labels", { body })));
}

export function useEditLabel() {
  return useAtlasMutation(async ({ id, ...body }: components["schemas"]["LabelPatch"] & { id: string }) =>
    unwrap(await api.PATCH("/atlas/curation/labels/{label_id}", { params: { path: { label_id: id } }, body })),
  );
}

export type LabelAction = "approve" | "reject" | "deprecate";

export function useLabelAction() {
  return useAtlasMutation(async ({ id, action }: { id: string; action: LabelAction }) => {
    const path = { params: { path: { label_id: id } } };
    if (action === "approve") return unwrap(await api.POST("/atlas/curation/labels/{label_id}/approve", path));
    if (action === "reject") return unwrap(await api.POST("/atlas/curation/labels/{label_id}/reject", { ...path, body: {} }));
    return unwrap(await api.POST("/atlas/curation/labels/{label_id}/deprecate", path));
  });
}

export function useMergeLabel() {
  return useAtlasMutation(async ({ id, into }: { id: string; into: string }) =>
    unwrap(await api.POST("/atlas/curation/labels/{label_id}/merge", { params: { path: { label_id: id } }, body: { into_id: into } })),
  );
}

export function useCreateDomain() {
  return useAtlasMutation(async (body: components["schemas"]["DomainIn"]) => unwrap(await api.POST("/admin/atlas/domains", { body })));
}

export function useUpdateDomain() {
  return useAtlasMutation(async ({ id, ...body }: components["schemas"]["DomainPatch"] & { id: string }) =>
    unwrap(await api.PATCH("/admin/atlas/domains/{domain_id}", { params: { path: { domain_id: id } }, body })),
  );
}

// --- Errors -------------------------------------------------------------------------------------

export type AtlasErrorKey = keyof Messages["atlas"]["errors"];

const CODES = [
  "label_exists",
  "domain_exists",
  "domain_archived",
  "invalid_name",
  "too_many_synonyms",
  "label_closed",
  "not_proposed",
  "not_active",
  "different_domain",
  "target_not_active",
  "merge_into_self",
  "label_not_found",
  "domain_not_found",
  "rate_limited",
] as const satisfies readonly AtlasErrorKey[];

export function atlasErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return apiErrorCode(error) ?? (error.status === 429 ? "rate_limited" : null);
}

/** The key in `atlas.errors`; `fallback` when the code is unknown. */
export function atlasErrorKey(error: unknown, fallback: AtlasErrorKey = "failed"): AtlasErrorKey {
  const code = knownCode(CODES, atlasErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 422) return "invalid";
  return fallback;
}
