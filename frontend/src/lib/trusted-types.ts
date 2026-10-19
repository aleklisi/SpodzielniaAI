/**
 * Trusted Types "default" policy (6.1, 6.2). The CSP requires Trusted Types for every script
 * sink (`require-trusted-types-for 'script'`), so any assignment of a plain string to one
 * goes through this policy:
 *
 * - script URLs: only our own chunks (same origin, under /_next/static/). Next.js loads them by
 *   assigning `script.src`; the value goes back unchanged, Next.js finds chunks by exact `src`;
 * - HTML (`innerHTML`, `DOMParser`, …): sanitised by DOMPurify, never refused. The editor
 *   (ProseMirror paste, BlockNote's Markdown conversion) parses HTML through these sinks.
 *   Whatever arrives, scripts, event handlers and `javascript:` URLs are stripped;
 * - script strings (`eval`-like sinks): no `createScript`, so always blocked.
 *
 * DOMPurify runs its own parsing through its "dompurify" policy (allowed by the CSP), so the
 * default policy is never re-entered.
 */

import DOMPurify from "dompurify";

export function allowedScriptUrl(value: string, location: { href: string; origin: string }): string | null {
  let url: URL;
  try {
    url = new URL(value, location.href);
  } catch {
    return null;
  }
  return url.origin === location.origin && url.pathname.startsWith("/_next/static/") ? value : null;
}

export type Sanitize = (html: string) => string;

export const sanitizeHtml: Sanitize = (html) =>
  DOMPurify.sanitize(html, { RETURN_TRUSTED_TYPE: false, FORBID_TAGS: ["style", "form"], FORBID_ATTR: ["style"] });

interface TrustedTypesFactory {
  createPolicy(
    name: string,
    rules: { createScriptURL?: (value: string) => string | null; createHTML?: (value: string) => string },
  ): unknown;
  defaultPolicy?: unknown;
}

export function installDefaultPolicy(
  win: { location: Location | { href: string; origin: string }; trustedTypes?: unknown },
  sanitize: Sanitize = sanitizeHtml,
): boolean {
  // A policy may return null to block an assignment; the DOM typings only allow strings.
  const factory = win.trustedTypes as TrustedTypesFactory | undefined;
  if (!factory || factory.defaultPolicy) return false;
  factory.createPolicy("default", {
    createScriptURL: (value) => allowedScriptUrl(value, win.location),
    createHTML: (value) => sanitize(value),
  });
  return true;
}
