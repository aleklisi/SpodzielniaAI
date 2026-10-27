"use client";

/**
 * Photos in the private notebook (9.2/9.3): image URLs and annotations. Owner only; the API
 * answers 404 for anyone else. Coordinates are fractions (0..1) of the stored image, so they
 * don't depend on the size the photo is displayed at.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, API_BASE, ApiError } from "@/lib/api";
import { apiErrorCode, knownCode } from "@/lib/messages";
import type { components } from "@/lib/api-schema";
import { keys as notebookKeys } from "@/lib/notebook";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

export type Photo = components["schemas"]["PhotoOut"];
export type Region = components["schemas"]["RegionOut"];
export type PhotoInput = components["schemas"]["PhotoIn"];

export const IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/heic"]);

/** A mark being edited: `key` is stable while editing (new marks have no id yet). */
export interface Mark {
  key: string;
  label: { id: string; name: string; status: Region["label"]["status"] } | null;
  x: number;
  y: number;
  w: number;
  h: number;
  description: string;
  source: Region["source"];
}

export const MIN_SIDE = 0.005; // the API's lower bound (0.5 % of a side)
export const MAX_MARKS = 50;

export function imageUrl(noteId: string, attachmentId: string, size: "preview" | "full" = "preview"): string {
  const base = `${API_BASE}/notes/${encodeURIComponent(noteId)}/attachments/${encodeURIComponent(attachmentId)}/image`;
  return size === "full" ? `${base}?size=full` : base;
}

const photoKey = (noteId: string, attachmentId: string) => ["photo", noteId, attachmentId] as const;

export function usePhoto(noteId: string, attachmentId: string) {
  return useQuery({
    queryKey: photoKey(noteId, attachmentId),
    queryFn: async () =>
      unwrap(
        await api.GET("/notes/{note_id}/attachments/{attachment_id}/annotations", {
          params: { path: { note_id: noteId, attachment_id: attachmentId } },
        }),
      ),
  });
}

export function useSavePhoto(noteId: string, attachmentId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: PhotoInput) =>
      unwrap(
        await api.PUT("/notes/{note_id}/attachments/{attachment_id}/annotations", {
          params: { path: { note_id: noteId, attachment_id: attachmentId } },
          body,
        }),
      ),
    onSuccess: (photo) => {
      client.setQueryData(photoKey(noteId, attachmentId), photo);
      void client.invalidateQueries({ queryKey: notebookKeys.note(noteId) }); // the derivative changed
    },
  });
}

export function toMarks(regions: Region[]): Mark[] {
  return regions.map((r) => ({
    key: r.id,
    label: r.label,
    x: r.x,
    y: r.y,
    w: r.w,
    h: r.h,
    description: r.description ?? "",
    source: r.source,
  }));
}

export function toInput(domainId: string, caption: string, marks: Mark[], version: number): PhotoInput {
  return {
    domain_id: domainId,
    caption: caption.trim() || null,
    version,
    regions: marks.map((m) => ({
      label_id: m.label!.id,
      x: m.x,
      y: m.y,
      w: m.w,
      h: m.h,
      description: m.description.trim() || null,
      source: m.source,
    })),
  };
}

/** Keep a box inside the image and at least MIN_SIDE wide and high. */
export function clampBox(box: { x: number; y: number; w: number; h: number }) {
  const w = Math.min(Math.max(box.w, MIN_SIDE), 1);
  const h = Math.min(Math.max(box.h, MIN_SIDE), 1);
  const x = Math.min(Math.max(box.x, 0), 1 - w);
  const y = Math.min(Math.max(box.y, 0), 1 - h);
  return { x, y, w, h };
}

export type PhotoErrorKey = keyof Messages["notebook"]["photo"]["errors"];

const CODES = [
  "stale_version",
  "invalid_box",
  "too_many_regions",
  "label_not_usable",
  "label_not_found",
  "domain_archived",
  "domain_not_found",
  "photo_not_found",
  "caption_too_long",
  "description_too_long",
] as const satisfies readonly PhotoErrorKey[];

export function photoErrorCode(error: unknown): string | null {
  return apiErrorCode(error);
}

/** The key in `notebook.photo.errors` (`too_many_regions` takes `{max: MAX_MARKS}`). */
export function photoErrorKey(error: unknown): PhotoErrorKey {
  const code = knownCode(CODES, photoErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 422) return "invalid";
  return "failed";
}
