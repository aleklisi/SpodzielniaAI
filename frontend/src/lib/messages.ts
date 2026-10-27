/**
 * Message keys for the backend's machine-readable error codes (14.3). The texts live in the
 * catalogues; a code maps to a key (type-checked against the Polish catalogue) and the
 * component translates it, so a lib function never needs the current language.
 */

import { ApiError } from "@/lib/api";
import type { Messages } from "@/messages";

/** The `error` code of an API error's detail (`{detail: {error}}` or `{error}`), if any. */
export function apiErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  if (detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string") return detail.error;
  return null;
}

/** `code` if it is one of `known` (the catalogue's keys for that area). */
export function knownCode<K extends string>(known: readonly K[], code: string | null | undefined): K | null {
  return code && (known as readonly string[]).includes(code) ? (code as K) : null;
}

export type UploadErrorKey = keyof Messages["errors"]["upload"];
export type SsrfErrorKey = keyof Messages["errors"]["ssrf"];

const UPLOAD_CODES = [
  "executable",
  "extension_mismatch",
  "content_type_mismatch",
  "type_not_allowed",
  "macros_not_allowed",
  "pdf_encrypted",
  "pdf_corrupt",
  "image_corrupt",
  "image_too_many_pixels",
  "media_corrupt",
  "too_large",
  "infected",
  "archive_nested",
  "note_not_found",
] as const satisfies readonly UploadErrorKey[];

const SSRF_CODES = [
  "private_address",
  "internal_hostname",
  "scheme_not_allowed",
  "port_not_allowed",
  "credentials_in_url",
  "ip_encoding",
  "invalid_hostname",
  "invalid_url",
  "url_too_long",
] as const satisfies readonly SsrfErrorKey[];

export function uploadErrorKey(code: string | undefined, status: number): UploadErrorKey {
  const known = knownCode(UPLOAD_CODES, code);
  if (known) return known;
  if (status === 415) return "type_not_allowed";
  if (status === 413) return "too_large";
  return "failed";
}

export function ssrfErrorKey(reason: string | undefined): SsrfErrorKey {
  return knownCode(SSRF_CODES, reason) ?? "failed";
}
