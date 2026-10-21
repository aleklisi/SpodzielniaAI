"use client";

/** „Rozpoznaj” on a notebook photo (9.9): local model + verified atlas examples. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Recognition = components["schemas"]["RecognitionOut"];
export type Suggestion = components["schemas"]["SuggestionOut"];

const key = (noteId: string, attachmentId: string) => ["recognitions", noteId, attachmentId] as const;
const RUNNING = new Set(["PENDING", "PROCESSING"]);

export function useRecognitions(noteId: string, attachmentId: string) {
  return useQuery({
    queryKey: key(noteId, attachmentId),
    queryFn: async () =>
      unwrap(
        await api.GET("/notes/{note_id}/attachments/{attachment_id}/recognitions", {
          params: { path: { note_id: noteId, attachment_id: attachmentId } },
        }),
      ),
    refetchInterval: (query) => (RUNNING.has(query.state.data?.[0]?.status ?? "") ? 2000 : false),
  });
}

export function useRecognize(noteId: string, attachmentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["RecognizeIn"]) =>
      unwrap(
        await api.POST("/notes/{note_id}/attachments/{attachment_id}/recognize", {
          params: { path: { note_id: noteId, attachment_id: attachmentId } },
          body,
        }),
      ),
    onSettled: () => void client.invalidateQueries({ queryKey: key(noteId, attachmentId) }),
  });
}

export function isRunning(r: Recognition | undefined): boolean {
  return !!r && RUNNING.has(r.status);
}

const ERRORS: Record<string, string> = {
  domain_required: "Wybierz dziedzinę zdjęcia albo szukaj we wszystkich dziedzinach.",
  recognition_in_progress: "Rozpoznawanie już trwa.",
  domain_not_found: "Nie znaleziono dziedziny.",
  rate_limited: "Dzienny limit rozpoznawania został wyczerpany.",
};

export function recognitionError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Nie udało się uruchomić rozpoznawania.";
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  const code = detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string" ? detail.error : null;
  if (code && ERRORS[code]) return ERRORS[code];
  if (error.status === 429) return ERRORS.rate_limited;
  return "Nie udało się uruchomić rozpoznawania.";
}
