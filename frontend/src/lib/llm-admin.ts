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
import { unwrap } from "@/lib/query";

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

export const PURPOSES: Record<Purpose, { label: string; hint: string }> = {
  RAG: { label: "Czat spółdzielni (RAG)", hint: "Odpowiedzi z kanonicznej wiedzy Wiki." },
  PROMOTION: { label: "Wnioski do Wiki", hint: "Przekształcanie notatek w szkic artykułu." },
  PRIVATE_NOTE: { label: "Prywatny notatnik", hint: "Pytania do własnych notatek." },
  PII_SCRUB: { label: "Anonimizacja", hint: "Usuwanie danych osobowych." },
  VLM: { label: "Opisy zdjęć", hint: "Model wizyjny dla załączników." },
  EMBED: { label: "Embeddingi", hint: "Wektory indeksu wiedzy (zmiana wymaga reindeksacji)." },
  TRANSCRIBE: { label: "Transkrypcja nagrań", hint: "Zamiana mowy na tekst w nagraniach z notatek." },
};

export const PURPOSE_ORDER: Purpose[] = ["RAG", "PROMOTION", "PRIVATE_NOTE", "PII_SCRUB", "VLM", "TRANSCRIBE", "EMBED"];

export const CAPABILITIES: Record<Capability, string> = {
  CHAT: "Czat",
  VLM: "Wizyjny",
  EMBED: "Embeddingi",
  TRANSCRIBE: "Mowa na tekst",
};

/** Models that generate text from a prompt: temperature and token limit apply. */
export function generates(capability: Capability): boolean {
  return capability === "CHAT" || capability === "VLM";
}

export const REVISION_STATUS: Record<Revision["status"], string> = {
  DRAFT: "Szkic",
  ACTIVE: "Aktywna",
  ARCHIVED: "Archiwalna",
};

const ERRORS: Record<string, string> = {
  endpoint_not_allowed: "Ten adres nie jest na liście dozwolonych serwerów (ustawia ją administrator serwera w .env).",
  cloud_requires_https: "Serwer chmurowy musi używać https:// na porcie 443.",
  credentials_in_url: "Adres nie może zawierać loginu ani hasła; klucz API podaj w osobnym polu.",
  invalid_url: "Nieprawidłowy adres.",
  name_taken: "Ta nazwa jest już zajęta.",
  provider_type_not_allowed: "Ten typ dostawcy nie jest obsługiwany.",
  provider_in_use: "Dostawca jest używany przez aktywną konfigurację.",
  provider_disabled: "Dostawca jest wyłączony.",
  model_in_use: "Model jest używany przez aktywną konfigurację.",
  model_exists: "Ten model jest już dodany.",
  model_disabled: "Model lub jego dostawca jest wyłączony.",
  embed_dimension_only_for_embed: "Wymiar wektora dotyczy tylko modeli embeddingów.",
  capability_mismatch: "Ten model nie nadaje się do tego zastosowania.",
  provider_not_cleared:
    "To zastosowanie przetwarza dane prywatne: wybierz model dostawcy dopuszczonego do danych prywatnych.",
  endpoint_not_clearable:
    "Tego adresu nie ma na liście serwerów, które mogą otrzymywać dane prywatne (LLM_PRIVATE_DATA_ENDPOINTS w .env).",
  provider_cleared: "Dostawca jest dopuszczony do danych prywatnych: najpierw cofnij dopuszczenie, potem zmień adres.",
  cloud_not_allowed: "Modele chmurowe są wyłączone w konfiguracji serwera (LLM_ALLOW_CLOUD).",
  probe_required: "Najpierw przetestuj model — aktywacja wymaga udanego testu z ostatnich godzin.",
  embed_model_unavailable: "Model embeddingów jest niedostępny albo jego dostawca nie jest dopuszczony do danych prywatnych.",
  generation_not_found: "Ta wersja indeksu nie istnieje.",
  generation_not_building: "Tę wersję indeksu już zbudowano albo anulowano.",
  not_draft: "Tę rewizję już aktywowano; zmiany wprowadzaj w nowym szkicu.",
  not_archived: "Przywrócić można tylko rewizję archiwalną.",
  revision_not_found: "Rewizja nie istnieje.",
  model_not_found: "Model nie istnieje.",
  provider_not_found: "Dostawca nie istnieje.",
  rate_limited: "Limit testów na godzinę został wyczerpany. Spróbuj później.",
  prompt_or_preset: "Podaj własne polecenie albo wybierz gotowy scenariusz.",
  duplicate_models: "Każdy model można wybrać tylko raz.",
};

export function llmErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.detail as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string") return detail.error;
  return error.status === 429 ? "rate_limited" : null;
}

export function llmError(error: unknown, fallback = "Operacja nie powiodła się."): string {
  const code = llmErrorCode(error);
  if (code && ERRORS[code]) return ERRORS[code];
  if (error instanceof ApiError && error.status === 422 && !code) return "Sprawdź wprowadzone wartości.";
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
