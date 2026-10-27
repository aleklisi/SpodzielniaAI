"use client";

/**
 * "Where your data is processed" (10.8, P4): the providers cleared for private data, and the
 * one-time banner after a new cloud provider is cleared.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Processors = components["schemas"]["ProcessorsOut"];
export type Processor = components["schemas"]["ProcessorOut"];
export type PrivatePurpose = Processor["purposes"][number];

/** What each private purpose means for a member: `privacy.purpose.<purpose>` in the catalogue. */

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
