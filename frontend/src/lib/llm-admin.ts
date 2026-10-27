"use client";

/**
 * LLM configuration console (7.6): providers, models, probes, revisions, playground. ADMIN + MFA
 * on every call (the API enforces both). The rules (private purposes need providers cleared for
 * private data (10.1), the cloud ceiling, allowlists, probes before activation) are the
 * backend's; the UI explains them up front.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { knownCode } from "@/lib/messages";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

export type LlmStatus = components["schemas"]["StatusOut"];
export type Provider = components["schemas"]["ProviderOut"];
export type ProviderInput = components["schemas"]["ProviderIn"];
export type ProviderPatch = components["schemas"]["ProviderPatch"];
export type Clearance = components["schemas"]["ClearanceIn"];
export type LlmModel = components["schemas"]["ModelOut"];
export type ModelInput = components["schemas"]["ModelIn"];
export type ModelPatch = components["schemas"]["ModelPatch"];
export type Revision = components["schemas"]["RevisionOut"];
export type Connection = components["schemas"]["ConnectionOut"];
export type Preset = components["schemas"]["PresetOut"];
export type PlaygroundResult = components["schemas"]["PlaygroundResultOut"];
export type Purpose = LlmStatus["purposes"][number]["purpose"];
export type Capability = LlmModel["capability"];
export type IndexState = components["schemas"]["IndexOut"];
export type IndexGeneration = components["schemas"]["GenerationOut"];

const KEY = ["admin", "llm"] as const;

export function useLlmStatus() {
  return useQuery({ queryKey: [...KEY, "status"], queryFn: async () => unwrap(await api.GET("/admin/llm/status")) });
}

export function useProviders() {
  return useQuery({ queryKey: [...KEY, "providers"], queryFn: async () => unwrap(await api.GET("/admin/llm/providers")) });
}

export function useModels() {
  return useQuery({ queryKey: [...KEY, "models"], queryFn: async () => unwrap(await api.GET("/admin/llm/models")) });
}

export function useRevisions() {
  return useQuery({ queryKey: [...KEY, "revisions"], queryFn: async () => unwrap(await api.GET("/admin/llm/revisions")) });
}

/** Index generations (10.3/10.6); polled while one is being built. */
export function useIndex() {
  return useQuery({
    queryKey: [...KEY, "index"],
    queryFn: async () => unwrap(await api.GET("/admin/llm/index")),
    refetchInterval: (query) => {
      const data = query.state.data;
      const building = data && [...data.generations, ...data.atlas_generations].some((g) => g.status === "BUILDING");
      return building ? 5_000 : false;
    },
  });
}

export function useRebuildIndex() {
  return useLlmMutation(async () => unwrap(await api.POST("/admin/llm/index/rebuild")));
}

export function useCancelGeneration() {
  return useLlmMutation(async (number: number) =>
    unwrap(await api.POST("/admin/llm/index/{number}/cancel", { params: { path: { number } } })),
  );
}

/** Seconds left for a build, from its pace so far (``null`` before it has a pace). */
export function eta(g: IndexGeneration, now: number = Date.now()): number | null {
  const elapsed = (now - new Date(g.created_at).getTime()) / 1000;
  if (g.done <= 0 || elapsed <= 0 || g.total <= g.done) return g.total <= g.done && g.done > 0 ? 0 : null;
  return Math.round(((g.total - g.done) * elapsed) / g.done);
}

export function usePresets() {
  return useQuery({
    queryKey: [...KEY, "presets"],
    staleTime: Infinity,
    queryFn: async () => unwrap(await api.GET("/admin/llm/playground/presets")),
  });
}

/** Every write refreshes the whole console: models, routes and status depend on each other. */
function useLlmMutation<I, O>(fn: (input: I) => Promise<O>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => void client.invalidateQueries({ queryKey: KEY }) });
}

export function useCreateProvider() {
  return useLlmMutation(async (body: ProviderInput) => unwrap(await api.POST("/admin/llm/providers", { body })));
}

export function useUpdateProvider() {
  return useLlmMutation(async ({ id, patch }: { id: string; patch: ProviderPatch }) =>
    unwrap(await api.PATCH("/admin/llm/providers/{provider_id}", { params: { path: { provider_id: id } }, body: patch })),
  );
}

export function useProviderDisabled() {
  return useLlmMutation(async ({ id, disabled }: { id: string; disabled: boolean }) =>
    unwrap(await api.POST("/admin/llm/providers/{provider_id}/disabled", { params: { path: { provider_id: id } }, body: { disabled } })),
  );
}

/** Clearing needs a recent login: a stale one rejects with ``StepUpRequired``. */
export function useClearProvider() {
  return useLlmMutation(async ({ id, clearance }: { id: string; clearance: Clearance }) =>
    unwrap(await api.POST("/admin/llm/providers/{provider_id}/clearance", { params: { path: { provider_id: id } }, body: clearance })),
  );
}

export function useUnclearProvider() {
  return useLlmMutation(async (id: string) =>
    unwrap(await api.DELETE("/admin/llm/providers/{provider_id}/clearance", { params: { path: { provider_id: id } } })),
  );
}

/** 11.1: deleted, or archived if a past revision used it; the list refreshes either way. */
export function useDeleteProvider() {
  return useLlmMutation(async (id: string) => {
    const result = await api.DELETE("/admin/llm/providers/{provider_id}", { params: { path: { provider_id: id } } });
    if (!result.response.ok) throw new ApiError(result.response.status, result.error);
  });
}

type Removal = keyof Messages["adminLlm"]["removal"];

/** Why "Remove" is unavailable (``ProviderOut.removal_blocked``): a key in `adminLlm.removal`
 * and its values, shown next to the button. */
export function removalBlocked(
  provider: Pick<Provider, "removal_blocked" | "removal_draft">,
): { key: Removal; values?: { number: number } } | null {
  switch (provider.removal_blocked) {
    case null:
    case undefined:
      return null;
    case "provider_in_use":
    case "provider_in_index":
      return { key: provider.removal_blocked };
    case "provider_in_draft":
      return provider.removal_draft ? { key: "inDraftN", values: { number: provider.removal_draft } } : { key: "provider_in_draft" };
    default:
      return { key: "other" };
  }
}

export function useTestProvider() {
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST("/admin/llm/providers/{provider_id}/test", { params: { path: { provider_id: id } } })),
  });
}

export function useCreateModel() {
  return useLlmMutation(async (body: ModelInput) => unwrap(await api.POST("/admin/llm/models", { body })));
}

export function useUpdateModel() {
  return useLlmMutation(async ({ id, patch }: { id: string; patch: ModelPatch }) =>
    unwrap(await api.PATCH("/admin/llm/models/{model_id}", { params: { path: { model_id: id } }, body: patch })),
  );
}

export function useModelDisabled() {
  return useLlmMutation(async ({ id, disabled }: { id: string; disabled: boolean }) =>
    unwrap(await api.POST("/admin/llm/models/{model_id}/disabled", { params: { path: { model_id: id } }, body: { disabled } })),
  );
}

export function useProbe() {
  return useLlmMutation(async (id: string) =>
    unwrap(await api.POST("/admin/llm/models/{model_id}/probe", { params: { path: { model_id: id } } })),
  );
}

export function useCreateDraft() {
  return useLlmMutation(async (input: { basedOn?: number; comment?: string }) =>
    unwrap(await api.POST("/admin/llm/revisions", { body: { based_on: input.basedOn ?? null, comment: input.comment ?? null } })),
  );
}

export function useSetRouting() {
  return useLlmMutation(async ({ number, routes }: { number: number; routes: Partial<Record<Purpose, string | null>> }) =>
    unwrap(
      await api.PUT("/admin/llm/revisions/{number}/routing", {
        params: { path: { number } },
        body: { routes: routes as Record<string, string | null> },
      }),
    ),
  );
}

export function useDeleteDraft() {
  return useLlmMutation(async (number: number) => {
    const result = await api.DELETE("/admin/llm/revisions/{number}", { params: { path: { number } } });
    if (!result.response.ok) throw new ApiError(result.response.status, result.error);
  });
}

export function useActivate() {
  return useLlmMutation(async (number: number) =>
    unwrap(await api.POST("/admin/llm/revisions/{number}/activate", { params: { path: { number } } })),
  );
}

export function useRollback() {
  return useLlmMutation(async ({ number, comment }: { number: number; comment?: string }) =>
    unwrap(await api.POST("/admin/llm/revisions/{number}/rollback", { params: { path: { number } }, body: { comment: comment ?? null } })),
  );
}

export function usePlayground() {
  return useMutation({
    mutationFn: async (body: { model_ids: string[]; prompt?: string; preset?: string }) =>
      unwrap(await api.POST("/admin/llm/playground", { body: { model_ids: body.model_ids, prompt: body.prompt ?? null, preset: body.preset ?? null } })),
  });
}

// --- Labels -------------------------------------------------------------------------------------

/** Names and hints in `adminLlm.purposes.<purpose>`. */
export const PURPOSE_ORDER: Purpose[] = ["RAG", "PROMOTION", "PRIVATE_NOTE", "PII_SCRUB", "VLM", "OCR", "TRANSCRIBE", "EMBED", "IMAGE_EMBED"];

/** Every capability, in order; names in `adminLlm.capabilities`. */
export const CAPABILITIES: Capability[] = ["CHAT", "VLM", "EMBED", "TRANSCRIBE", "IMAGE_EMBED"];

/** Models that generate text from a prompt: temperature and token limit apply. */
export function generates(capability: Capability): boolean {
  return capability === "CHAT" || capability === "VLM";
}


export type LlmErrorKey = keyof Messages["adminLlm"]["errors"];

const CODES = [
  "endpoint_not_allowed",
  "cloud_requires_https",
  "credentials_in_url",
  "invalid_url",
  "name_taken",
  "provider_type_not_allowed",
  "provider_in_use",
  "provider_in_index",
  "provider_in_draft",
  "provider_removed",
  "provider_disabled",
  "model_in_use",
  "model_exists",
  "model_disabled",
  "embed_dimension_only_for_embed",
  "capability_mismatch",
  "provider_not_cleared",
  "endpoint_not_clearable",
  "provider_cleared",
  "cloud_not_allowed",
  "probe_required",
  "embed_model_unavailable",
  "generation_not_found",
  "generation_not_building",
  "not_draft",
  "not_archived",
  "revision_not_found",
  "model_not_found",
  "provider_not_found",
  "rate_limited",
  "prompt_or_preset",
  "duplicate_models",
] as const satisfies readonly LlmErrorKey[];

export function llmErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.detail as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string") return detail.error;
  return error.status === 429 ? "rate_limited" : null;
}

/** The key in `adminLlm.errors`; `fallback` when the code is unknown. */
export function llmErrorKey(error: unknown, fallback: LlmErrorKey = "failed"): LlmErrorKey {
  const code = llmErrorCode(error);
  const known = knownCode(CODES, code);
  if (known) return known;
  if (error instanceof ApiError && error.status === 422 && !code) return "invalid";
  return fallback;
}

/** The models a purpose may use: the right capability, enabled, and cleared for private purposes. */
export function eligibleModels(
  purpose: { capability: Capability; private_data: boolean },
  models: LlmModel[],
  providers: Provider[],
): LlmModel[] {
  const byId = new Map(providers.map((p) => [p.id, p]));
  return models.filter((m) => {
    const provider = byId.get(m.provider_id);
    if (!provider || provider.disabled || !provider.allowed || m.disabled) return false;
    if (m.capability !== purpose.capability) return false;
    return !purpose.private_data || provider.private_data_cleared;
  });
}

export function modelLabel(model: LlmModel | undefined, providers: Provider[]): string {
  if (!model) return "—";
  const provider = providers.find((p) => p.id === model.provider_id);
  return `${provider?.name ?? "?"}/${model.model_name}`;
}

/** A probe is fresh enough for activation (the backend checks again). */
export function probeFresh(model: LlmModel, maxAgeHours: number, now = Date.now()): boolean {
  if (!model.last_probe_ok || !model.last_probe_at) return false;
  return now - new Date(model.last_probe_at).getTime() <= maxAgeHours * 3_600_000;
}
