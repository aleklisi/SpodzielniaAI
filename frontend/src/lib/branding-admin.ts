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
import { knownCode } from "@/lib/messages";
import type { Messages } from "@/messages";

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

export type BrandingErrorKey = keyof Messages["adminBranding"]["errors"];

const CODES = [
  "colours_together",
  "invalid_colour",
  "contrast_unreachable",
  "name_too_long",
  "name_invalid",
  "type_not_allowed",
  "logo_type",
  "extension_mismatch",
  "content_type_mismatch",
  "executable",
  "too_large",
  "dimensions",
  "unreadable_image",
  "infected",
  "scanner_unavailable",
] as const satisfies readonly BrandingErrorKey[];

/** The key in `adminBranding.errors`. */
export function brandingErrorKey(error: unknown): BrandingErrorKey {
  if (error instanceof RateLimited) return "rate_limited";
  if (error instanceof StepUpRequired) return "mfa";
  if (error instanceof ApiError) {
    const body = error.detail as { detail?: { error?: string } | unknown[] } | undefined;
    const code = knownCode(CODES, body && !Array.isArray(body.detail) ? body.detail?.error : undefined);
    if (code) return code;
    if (Array.isArray(body?.detail)) return "invalid";
  }
  return "failed";
}
