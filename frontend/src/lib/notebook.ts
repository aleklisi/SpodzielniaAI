"use client";

/** Notebook queries and mutations (6.2). Every call is the member's own data: the backend
 * scopes it by RLS, and another member's note answers 404 exactly like a missing one. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type NoteSummary = components["schemas"]["NoteSummaryOut"];
export type Note = components["schemas"]["NoteOut"];
export type Derivative = components["schemas"]["DerivativeOut"];
export type Promotion = components["schemas"]["PromotionOut"];
export type NoteAnswer = components["schemas"]["NoteAnswer"];

export const keys = {
  notes: ["notes"] as const,
  note: (id: string) => ["notes", id] as const,
  attachment: (noteId: string, id: string) => ["notes", noteId, "attachments", id] as const,
  promotion: (id: string) => ["promotions", id] as const,
};

const TERMINAL = new Set(["DONE", "FAILED", "QUARANTINED"]);

export function isProcessing(derivatives: Derivative[]): boolean {
  return derivatives.some((d) => !TERMINAL.has(d.status));
}

export function useNotes() {
  return useQuery({ queryKey: keys.notes, queryFn: async () => unwrap(await api.GET("/notes")) });
}

export function useNote(noteId: string) {
  return useQuery({
    queryKey: keys.note(noteId),
    queryFn: async () => unwrap(await api.GET("/notes/{note_id}", { params: { path: { note_id: noteId } } })),
    // While anything is still being transcribed, described or parsed, look again.
    refetchInterval: (query) => (query.state.data && isProcessing(query.state.data.derivatives) ? 3000 : false),
  });
}

export function useCreateNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (title: string) => unwrap(await api.POST("/notes", { body: { title, body: "" } })),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.notes }),
  });
}

export function useSaveNote(noteId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (change: { title?: string; body?: string }) =>
      unwrap(await api.PATCH("/notes/{note_id}", { params: { path: { note_id: noteId } }, body: change })),
    onSuccess: (note) => {
      client.setQueryData(keys.note(noteId), note);
      void client.invalidateQueries({ queryKey: keys.notes, exact: true });
    },
  });
}

export function useDeleteNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (noteId: string) => {
      const result = await api.DELETE("/notes/{note_id}", { params: { path: { note_id: noteId } } });
      if (!result.response.ok) unwrap(result);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: keys.notes }),
  });
}

export type LinkResult = { ok: true; derivative: Derivative } | { ok: false; reason: string | undefined };

export function useAddLink(noteId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (url: string): Promise<LinkResult> => {
      const result = await api.POST("/notes/{note_id}/links", {
        params: { path: { note_id: noteId } },
        body: { url },
      });
      if (result.response.status === 400) {
        const detail = (result.error as { detail?: { error?: string; reason?: string } } | undefined)?.detail;
        if (detail?.error === "ssrf_blocked") return { ok: false, reason: detail.reason };
      }
      return { ok: true, derivative: unwrap(result) };
    },
    onSuccess: () => client.invalidateQueries({ queryKey: keys.note(noteId) }),
  });
}

export function useConfirmQuarantine(noteId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (derivativeId: string) =>
      unwrap(
        await api.POST("/notes/{note_id}/derivatives/{derivative_id}/confirm-quarantine", {
          params: { path: { note_id: noteId, derivative_id: derivativeId } },
        }),
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.note(noteId) }),
  });
}

export function usePromote() {
  return useMutation({
    mutationFn: async (input: { noteIds: string[]; titleHint: string }) =>
      unwrap(
        await api.POST("/notes/promote-to-wiki", {
          body: {
            note_ids: input.noteIds,
            attachment_derivative_ids: [],
            title_hint: input.titleHint.trim() || null,
          },
        }),
      ),
  });
}

export function usePromotion(promotionId: string | null) {
  return useQuery({
    queryKey: keys.promotion(promotionId ?? ""),
    enabled: promotionId !== null,
    queryFn: async () =>
      unwrap(await api.GET("/promotions/{promotion_id}", { params: { path: { promotion_id: promotionId ?? "" } } })),
    refetchInterval: (query) => (query.state.data && ["DONE", "FAILED"].includes(query.state.data.status) ? false : 3000),
  });
}

export function useAskNotebook() {
  return useMutation({
    mutationFn: async (question: string) => unwrap(await api.POST("/notes/ask", { body: { question } })),
  });
}

/** Character spans the injection scanner flagged (offsets into the derivative's content). */
export interface FlaggedSpan {
  start: number;
  end: number;
  kind: string;
  rule?: string;
}

export function flaggedSpans(derivative: Derivative): FlaggedSpan[] {
  const scan = (derivative.injection_flags as { scan?: { flags?: unknown[] } }).scan;
  const length = derivative.content?.length ?? 0;
  const spans: FlaggedSpan[] = [];
  for (const raw of scan?.flags ?? []) {
    const flag = raw as { start?: unknown; end?: unknown; kind?: unknown; rule?: unknown };
    if (typeof flag.start !== "number" || typeof flag.end !== "number") continue;
    const start = Math.max(0, Math.min(flag.start, length));
    const end = Math.max(start, Math.min(flag.end, length));
    if (end > start) {
      spans.push({ start, end, kind: String(flag.kind ?? ""), rule: typeof flag.rule === "string" ? flag.rule : undefined });
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

/** `text` cut into plain and flagged pieces (overlapping spans merged). */
export function segment(text: string, spans: FlaggedSpan[]): Array<{ text: string; flagged: boolean }> {
  const pieces: Array<{ text: string; flagged: boolean }> = [];
  let cursor = 0;
  for (const span of spans) {
    const start = Math.max(span.start, cursor);
    if (span.end <= cursor) continue;
    if (start > cursor) pieces.push({ text: text.slice(cursor, start), flagged: false });
    pieces.push({ text: text.slice(start, span.end), flagged: true });
    cursor = span.end;
  }
  if (cursor < text.length) pieces.push({ text: text.slice(cursor), flagged: false });
  return pieces;
}
