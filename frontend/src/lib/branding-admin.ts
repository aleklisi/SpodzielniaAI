"use client";

/**
 * The „Wygląd” console (13.5): the organisation's name, logo and two colours. ADMIN + MFA on
 * every call (the API enforces both). The palette is always derived by the server
 * (`POST /admin/branding/preview`): the browser never computes colours.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, RateLimited, StepUpRequired } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { TOKENS } from "@/lib/branding-css";
import { unwrap } from "@/lib/query";

export type AdminBranding = components["schemas"]["AdminBrandingOut"];
export type Preview = components["schemas"]["PreviewOut"];
export type BrandingInput = components["schemas"]["BrandingIn"];

const KEY = ["admin", "branding"] as const;

export function useAdminBranding() {
  return useQuery({ queryKey: KEY, queryFn: async () => unwrap(await api.GET("/admin/branding")) });
}

export function usePreview() {
  return useMutation({
    mutationFn: async (colours: { brand: string; accent: string }) =>
      unwrap(await api.POST("/admin/branding/preview", { body: colours })),
  });
}

function useWrite<A>(write: (arg: A) => Promise<AdminBranding>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: write,
    onSuccess: (data) => {
      client.setQueryData(KEY, data);
      applyLive(data.palette);
    },
  });
}

export function useSaveBranding() {
  return useWrite(async (input: BrandingInput) => unwrap(await api.PUT("/admin/branding", { body: input })));
}

export function useUploadLogo() {
  return useWrite(async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return unwrap(
      await api.POST("/admin/branding/logo", {
        body: form as unknown as { file: string },
        bodySerializer: (body: unknown) => body as FormData,
      }),
    );
  });
}

export function useRemoveLogo() {
  return useWrite(async () => unwrap(await api.DELETE("/admin/branding/logo")));
}

export function useResetBranding() {
  return useWrite(async () => unwrap(await api.POST("/admin/branding/reset")));
}

/**
 * Show a saved palette in this tab at once (other pages load it from the layout within 10 s).
 * Through the CSSOM, which the CSP does not restrict, never a `style=` attribute; values are
 * checked again (#rrggbb only).
 */
export function applyLive(palette: AdminBranding["palette"]): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  const tokens = palette?.[dark ? "dark" : "light"];
  for (const token of TOKENS) {
    const value = tokens?.[token];
    if (value && /^#[0-9a-f]{6}$/.test(value)) root.style.setProperty(`--${token}`, value);
    else root.style.removeProperty(`--${token}`);
  }
  if (!palette) document.getElementById("branding")?.remove(); // back to the default colours
}

const ERRORS: Record<string, string> = {
  colours_together: "Wybierz oba kolory albo żaden.",
  invalid_colour: "Kolor musi mieć postać #rrggbb.",
  contrast_unreachable: "Z tym kolorem tekst nie byłby czytelny w żadnym odcieniu. Wybierz inny.",
  name_too_long: "Nazwa może mieć najwyżej 60 znaków.",
  name_invalid: "Nazwa zawiera niedozwolone znaki (np. znaki sterujące).",
  type_not_allowed: "Dozwolone są tylko obrazy PNG, JPEG lub WebP (SVG nie jest obsługiwany).",
  logo_type: "Dozwolone są tylko obrazy PNG, JPEG lub WebP.",
  extension_mismatch: "Rozszerzenie pliku nie pasuje do jego zawartości.",
  content_type_mismatch: "Typ pliku nie pasuje do jego zawartości.",
  executable: "Pliki wykonywalne są niedozwolone.",
  too_large: "Plik jest za duży (najwyżej 2 MB).",
  dimensions: "Obraz jest za duży (najwyżej 4096 × 4096 pikseli).",
  unreadable_image: "Nie udało się odczytać obrazu.",
  infected: "Plik został odrzucony przez skaner antywirusowy.",
  scanner_unavailable: "Skaner antywirusowy jest chwilowo niedostępny. Spróbuj za chwilę.",
};

export function brandingError(error: unknown): string {
  if (error instanceof RateLimited) return "Zbyt wiele zmian w ciągu godziny. Spróbuj później.";
  if (error instanceof StepUpRequired) return "Ta operacja wymaga zalogowania z MFA.";
  if (error instanceof ApiError) {
    const body = error.detail as { detail?: { error?: string } | unknown[] } | undefined;
    const code = body && !Array.isArray(body.detail) ? body.detail?.error : undefined;
    if (code && ERRORS[code]) return ERRORS[code];
    if (Array.isArray(body?.detail)) return "Nieprawidłowe dane w formularzu.";
  }
  return "Nie udało się zapisać zmian.";
}
