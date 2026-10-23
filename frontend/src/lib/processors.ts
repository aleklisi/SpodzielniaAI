"use client";

/**
 * „Gdzie przetwarzane są Twoje dane” (10.8, P4): the providers cleared for private data, and the
 * one-time banner after a new cloud provider is cleared.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Processors = components["schemas"]["ProcessorsOut"];
export type Processor = components["schemas"]["ProcessorOut"];
export type PrivatePurpose = Processor["purposes"][number];

/** What each private purpose means for a member (plain words, no model names). */
export const PURPOSE_TEXT: Record<PrivatePurpose, string> = {
  PII_SCRUB: "usuwanie danych osobowych z tekstów przed udostępnieniem",
  PRIVATE_NOTE: "odpowiedzi na pytania do Twojego notatnika",
  PROMOTION: "przygotowanie szkicu artykułu z Twoich notatek",
  VLM: "opisy zdjęć i sprawdzanie zdjęć przed wysłaniem do atlasu",
  EMBED: "wyszukiwanie w notatkach i w wiki (wektory tekstu)",
  TRANSCRIBE: "transkrypcja nagrań",
  OCR: "odczyt zeskanowanych dokumentów",
  IMAGE_EMBED: "atlas i „Rozpoznaj” (wektory zdjęć)",
  RAG: "odpowiedzi z wiki spółdzielni",
};

export function useProcessors(enabled = true) {
  return useQuery<Processors>({
    queryKey: ["processors"],
    staleTime: 5 * 60_000,
    enabled,
    retry: false,
    queryFn: async () => unwrap(await api.GET("/privacy/processors")),
  });
}

export function useAcknowledgeProcessors() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await api.POST("/privacy/processors/seen");
    },
    onSuccess: () => client.setQueryData<Processors>(["processors"], (old) => (old ? { ...old, banner: false } : old)),
  });
}
