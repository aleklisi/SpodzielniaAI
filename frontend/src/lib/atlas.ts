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

export type Domain = components["schemas"]["DomainOut"];
export type Label = components["schemas"]["LabelOut"];
export type CuratedLabel = components["schemas"]["CuratedLabelOut"];
export type LabelStatus = Label["status"];
export type DomainStatus = Domain["status"];

export const LABEL_STATUS: Record<LabelStatus, { label: string; tone: "ok" | "warn" | "neutral" | "danger" }> = {
  ACTIVE: { label: "Aktywna", tone: "ok" },
  PROPOSED: { label: "Propozycja", tone: "warn" },
  DEPRECATED: { label: "Wycofana", tone: "neutral" },
  REJECTED: { label: "Odrzucona", tone: "danger" },
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

const ERRORS: Record<string, string> = {
  label_exists: "Taka etykieta już istnieje w tej dziedzinie.",
  domain_exists: "Taka dziedzina już istnieje.",
  domain_archived: "Ta dziedzina jest zarchiwizowana: nie przyjmuje nowych etykiet ani zdjęć.",
  invalid_name: "Nazwa musi zawierać litery lub cyfry.",
  too_many_synonyms: "Najwyżej 20 synonimów.",
  label_closed: "Ta etykieta jest wycofana lub odrzucona.",
  not_proposed: "To nie jest propozycja (ktoś już ją rozpatrzył?).",
  not_active: "Wycofać można tylko aktywną etykietę.",
  different_domain: "Scalać można tylko etykiety z tej samej dziedziny.",
  target_not_active: "Etykieta docelowa musi być aktywna.",
  merge_into_self: "Nie można scalić etykiety z nią samą.",
  label_not_found: "Nie znaleziono etykiety.",
  domain_not_found: "Nie znaleziono dziedziny.",
  rate_limited: "Limit propozycji na dziś został wyczerpany.",
};

export function atlasErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  if (detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string") return detail.error;
  return error.status === 429 ? "rate_limited" : null;
}

export function atlasError(error: unknown, fallback = "Operacja nie powiodła się."): string {
  const code = atlasErrorCode(error);
  if (code && ERRORS[code]) return ERRORS[code];
  if (error instanceof ApiError && error.status === 422) return "Sprawdź wprowadzone wartości.";
  return fallback;
}
