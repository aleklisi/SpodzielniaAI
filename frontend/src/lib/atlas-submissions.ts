"use client";

/**
 * Sending a photo to the shared atlas (9.4). The server first prepares it (shared copy without
 * metadata, personal data removed from texts, checks); the member then sees exactly what would
 * be shared and sends it or withdraws it. Nothing is visible to others before that.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_BASE, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Submission = components["schemas"]["SubmissionOut"];
export type AtlasDraft = components["schemas"]["DraftOut"];
export type SendInput = components["schemas"]["SendIn"];

export function atlasImageUrl(imageId: string, size: "thumb" | "preview" | "full" = "preview"): string {
  return `${API_BASE}/atlas/images/${encodeURIComponent(imageId)}/image?size=${size}`;
}

const key = (attachmentId: string) => ["atlas", "submissions", attachmentId] as const;

/** The photo's submissions, newest first; polls while one is being prepared. */
export function usePhotoSubmissions(attachmentId: string) {
  return useQuery({
    queryKey: key(attachmentId),
    queryFn: async () => unwrap(await api.GET("/atlas/submissions", { params: { query: { attachment_id: attachmentId } } })),
    refetchInterval: (query) => (query.state.data?.[0]?.status === "PENDING" ? 2000 : false),
  });
}

export function useRequestSubmission(noteId: string, attachmentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/atlas/submissions", { body: { note_id: noteId, attachment_id: attachmentId } })),
    onSettled: () => void client.invalidateQueries({ queryKey: key(attachmentId) }),
  });
}

export function useSendSubmission(attachmentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: SendInput & { id: string }) =>
      unwrap(await api.POST("/atlas/submissions/{submission_id}/send", { params: { path: { submission_id: id } }, body })),
    onSettled: () => void client.invalidateQueries({ queryKey: key(attachmentId) }),
  });
}

export function useWithdrawSubmission(attachmentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST("/atlas/submissions/{submission_id}/withdraw", { params: { path: { submission_id: id } } })),
    onSettled: () => void client.invalidateQueries({ queryKey: key(attachmentId) }),
  });
}

export const PRIVACY_LABEL: Record<string, string> = {
  people: "osoby lub części ciała",
  faces: "rozpoznawalne twarze",
  documents: "dokumenty lub czytelny tekst",
  plates: "tablice rejestracyjne",
  addresses: "numery domów lub szyldy z nazwiskami",
};

export const ATLAS_STATUS: Record<AtlasDraft["status"], { label: string; tone: "ok" | "warn" | "neutral" | "danger" | "primary" }> = {
  DRAFT: { label: "Przygotowane, niewysłane", tone: "neutral" },
  IN_REVIEW: { label: "W recenzji ekspertów", tone: "primary" },
  APPROVED: { label: "W atlasie", tone: "ok" },
  REVISION_REQUIRED: { label: "Eksperci proszą o poprawki", tone: "warn" },
  REJECTED: { label: "Odrzucone przez ekspertów", tone: "danger" },
};

const ERRORS: Record<string, string> = {
  nothing_to_send: "Najpierw zaznacz i opisz co najmniej jeden element, potem zapisz zdjęcie.",
  submission_in_progress: "To zdjęcie jest już przygotowywane do atlasu.",
  already_in_atlas: "To zdjęcie jest już w atlasie lub w recenzji.",
  domain_archived: "Dziedzina zdjęcia jest zarchiwizowana.",
  label_not_usable: "Któraś etykieta nie jest już dostępna. Zmień ją i zapisz zdjęcie.",
  label_not_found: "Któraś etykieta nie należy do dziedziny zdjęcia.",
  duplicate_photo: "To zdjęcie (albo bardzo podobne) jest już w atlasie.",
  injection_detected: "W opisach są fragmenty wyglądające jak polecenia dla AI. Popraw opisy i spróbuj ponownie.",
  image_unreadable: "Nie udało się odczytać zdjęcia.",
  people_confirmation_required: "Potwierdź, że osoby na zdjęciu zgodziły się na jego udostępnienie lub są nierozpoznawalne.",
  not_prepared: "To zgłoszenie nie czeka już na wysłanie.",
  not_withdrawable: "Tego zgłoszenia nie można już wycofać.",
  rate_limited: "Dzienny limit zdjęć wysyłanych do atlasu został wyczerpany.",
};

export function submissionError(error: unknown, fallback = "Operacja nie powiodła się."): string {
  if (typeof error === "string") return ERRORS[error] ?? fallback;
  if (!(error instanceof ApiError)) return fallback;
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  const code = detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string" ? detail.error : null;
  if (code && ERRORS[code]) return ERRORS[code];
  if (error.status === 429) return ERRORS.rate_limited;
  return fallback;
}
