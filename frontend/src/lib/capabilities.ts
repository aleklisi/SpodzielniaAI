"use client";

/**
 * Which features have the models they need (10.2). A feature without one is shown as off with
 * „Model nie jest skonfigurowany”, never silently routed elsewhere. While the answer is loading
 * (or if it fails) features count as available: the API still refuses with 503.
 */

import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Feature =
  | "wiki_answers"
  | "notebook_answers"
  | "notebook_search"
  | "promotion"
  | "image_descriptions"
  | "recognition"
  | "transcription"
  | "ocr";

export const MODEL_MISSING = "Model nie jest skonfigurowany";

type Capabilities = components["schemas"]["CapabilitiesOut"];

export function useCapabilities() {
  return useQuery<Capabilities>({
    queryKey: ["capabilities"],
    staleTime: 60_000,
    queryFn: async () => unwrap(await api.GET("/capabilities")),
  });
}

export function useFeature(feature: Feature): boolean {
  const { data } = useCapabilities();
  return data?.features[feature] ?? true;
}

/** True for the API's 503 ``model_not_configured`` (the configuration changed meanwhile). */
export function isModelMissing(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 503) return false;
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  return !!detail && typeof detail === "object" && "error" in detail && detail.error === "model_not_configured";
}
