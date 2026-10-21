"use client";

/**
 * The shared atlas (9.5): approved photos for members, and the experts' review of photos sent to
 * the atlas. Viewing a photo page or a full image counts against the viewer's coverage budget
 * (like reading the wiki); lists show thumbnails only.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type PhotoCard = components["schemas"]["PhotoCardOut"];
export type PhotoDetail = components["schemas"]["PhotoDetailOut"];
export type QueuePhoto = components["schemas"]["QueuePhotoOut"];
export type ReviewPhoto = components["schemas"]["ReviewPhotoOut"];
export type ReviewDecision = components["schemas"]["DecisionIn"]["decision"];
export type RegionEditInput = components["schemas"]["RegionEditIn"];

export const PAGE_SIZE = 24;

export interface GalleryFilters {
  domainId: string;
  labelId: string;
  q: string;
  offset: number;
}

export function useGallery(filters: GalleryFilters) {
  return useQuery({
    queryKey: ["atlas", "gallery", filters],
    placeholderData: keepPreviousData,
    queryFn: async () =>
      unwrap(
        await api.GET("/atlas/photos", {
          params: {
            query: {
              domain_id: filters.domainId || undefined,
              label_id: filters.labelId || undefined,
              q: filters.q || undefined,
              offset: filters.offset,
              limit: PAGE_SIZE,
            },
          },
        }),
      ),
  });
}

export function useAtlasPhoto(id: string) {
  return useQuery({
    queryKey: ["atlas", "photo", id],
    retry: false,
    queryFn: async () => unwrap(await api.GET("/atlas/photos/{image_id}", { params: { path: { image_id: id } } })),
  });
}

// --- Review (EXPERT + MFA) ----------------------------------------------------------------------

const REVIEW = ["atlas", "review"] as const;

export function useAtlasQueue() {
  return useQuery({ queryKey: [...REVIEW, "queue"], queryFn: async () => unwrap(await api.GET("/atlas/review/queue")) });
}

export function useReviewPhoto(id: string | null) {
  return useQuery({
    queryKey: [...REVIEW, "photo", id],
    enabled: id !== null,
    queryFn: async () => unwrap(await api.GET("/atlas/review/{image_id}", { params: { path: { image_id: id ?? "" } } })),
  });
}

function useReviewMutation<I, O>(fn: (input: I) => Promise<O>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => void client.invalidateQueries({ queryKey: REVIEW }) });
}

export function useCorrectRegions(id: string) {
  return useReviewMutation(async ({ version, regions }: { version: number; regions: RegionEditInput[] }) =>
    unwrap(await api.PUT("/atlas/review/{image_id}/regions", { params: { path: { image_id: id } }, body: { version, regions } })),
  );
}

export function useAtlasDecision(id: string) {
  return useReviewMutation(async (body: components["schemas"]["DecisionIn"]) =>
    unwrap(await api.POST("/atlas/review/{image_id}/decision", { params: { path: { image_id: id } }, body })),
  );
}

const ERRORS: Record<string, string> = {
  stale_version: "Ktoś właśnie zmienił to zdjęcie. Wczytaj je ponownie.",
  not_in_review: "To zdjęcie nie czeka już na recenzję.",
  own_photo: "Nie recenzujesz własnych zdjęć.",
  already_approved: "Już zatwierdziłeś(-aś) tę wersję.",
  proposed_labels_pending: "Zdjęcie używa propozycji etykiet. Najpierw rozpatrz je w słowniku etykiet.",
  comment_required: "Napisz autorowi, co trzeba zmienić lub dlaczego zdjęcie jest odrzucane.",
  invalid_box: "Któreś oznaczenie wychodzi poza zdjęcie albo jest za małe.",
  label_not_usable: "Użyj aktywnej etykiety.",
  label_not_found: "Etykieta nie należy do dziedziny zdjęcia.",
  too_many_regions: "Najwyżej 50 oznaczeń.",
};

export function reviewError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Operacja nie powiodła się.";
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  const code = detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string" ? detail.error : null;
  return (code && ERRORS[code]) || "Operacja nie powiodła się.";
}
