"use client";

/** "Recognise" on a notebook photo (9.9): local model + verified atlas examples. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { apiErrorCode, knownCode } from "@/lib/messages";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

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

export type RecognitionErrorKey = keyof Messages["notebook"]["recognition"]["errors"];

const CODES = [
  "domain_required",
  "recognition_in_progress",
  "domain_not_found",
  "rate_limited",
  "model_not_configured",
] as const satisfies readonly RecognitionErrorKey[];

export function recognitionErrorKey(error: unknown): RecognitionErrorKey {
  const code = knownCode(CODES, apiErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 429) return "rate_limited";
  return "failed";
}
