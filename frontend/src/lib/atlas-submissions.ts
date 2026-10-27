"use client";

/**
 * Sending a photo to the shared atlas (9.4). The server first prepares it (shared copy without
 * metadata, personal data removed from texts, checks); the member then sees exactly what would
 * be shared and sends it or withdraws it. Nothing is visible to others before that.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_BASE, ApiError } from "@/lib/api";
import { apiErrorCode, knownCode } from "@/lib/messages";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

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

export type PrivacyFlag = keyof Messages["notebook"]["submission"]["privacy"];
export type SubmissionErrorKey = keyof Messages["notebook"]["submission"]["errors"];
type Tone = "ok" | "warn" | "neutral" | "danger" | "primary";

const PRIVACY_FLAGS = ["people", "faces", "documents", "plates", "addresses"] as const satisfies readonly PrivacyFlag[];

export function privacyFlag(flag: string): PrivacyFlag | null {
  return knownCode(PRIVACY_FLAGS, flag);
}

/** Labels in `common.atlasStatus`. */
export const ATLAS_STATUS_TONE: Record<AtlasDraft["status"], Tone> = {
  DRAFT: "neutral",
  IN_REVIEW: "primary",
  APPROVED: "ok",
  REVISION_REQUIRED: "warn",
  REJECTED: "danger",
};

const CODES = [
  "nothing_to_send",
  "submission_in_progress",
  "already_in_atlas",
  "domain_archived",
  "label_not_usable",
  "label_not_found",
  "duplicate_photo",
  "injection_detected",
  "image_unreadable",
  "people_confirmation_required",
  "not_prepared",
  "not_withdrawable",
  "rate_limited",
] as const satisfies readonly SubmissionErrorKey[];

/** The key in `notebook.submission.errors` for an API error or a stored error code. */
export function submissionErrorKey(error: unknown, fallback: SubmissionErrorKey = "failed"): SubmissionErrorKey {
  const code = knownCode(CODES, typeof error === "string" ? error : apiErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 429) return "rate_limited";
  return fallback;
}

// --- The author's atlas photos (9.6) -------------------------------------------------------------

export type MyPhoto = components["schemas"]["MyPhotoOut"];
export type PhotoConsentAction = "suspend" | "resume" | "revoke";

const MINE = ["atlas", "mine"] as const;

export function useMyAtlasPhotos() {
  return useQuery({ queryKey: MINE, queryFn: async () => unwrap(await api.GET("/me/contributions/atlas")) });
}

export function usePhotoConsent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, action }: { id: string; action: PhotoConsentAction }) => {
      const path = { params: { path: { image_id: id } } };
      if (action === "suspend") return unwrap(await api.POST("/me/contributions/atlas/{image_id}/suspend", path));
      if (action === "resume") return unwrap(await api.POST("/me/contributions/atlas/{image_id}/resume", path));
      return unwrap(await api.POST("/me/contributions/atlas/{image_id}/revoke", { ...path, body: { confirm: "REVOKE" } }));
    },
    onSettled: () => void client.invalidateQueries({ queryKey: ["atlas"] }),
  });
}

export function usePhotoPreferences() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: { id: string; show_author?: boolean; allow_export?: boolean }) =>
      unwrap(await api.PATCH("/me/contributions/atlas/{image_id}", { params: { path: { image_id: id } }, body })),
    onSettled: () => void client.invalidateQueries({ queryKey: ["atlas"] }),
  });
}

/** Labels in `common.photoConsent`. */
export const CONSENT_TONE: Record<MyPhoto["consent_status"], "ok" | "warn" | "neutral"> = {
  ACTIVE: "ok",
  SUSPENDED: "warn",
  REVOKED: "neutral",
};
