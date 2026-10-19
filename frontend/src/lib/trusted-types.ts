/**
 * Trusted Types "default" policy (6.1). The CSP requires Trusted Types for every script sink
 * (`require-trusted-types-for 'script'`). Next.js loads its own code chunks by assigning
 * `script.src`; this policy lets exactly those through (same origin, under /_next/static/) and
 * nothing else. There is no createHTML or createScript: HTML and script strings stay blocked
 * (sanitised Markdown/HTML gets its own "dompurify" policy, 6.5).
 */

export function allowedScriptUrl(value: string, location: { href: string; origin: string }): string | null {
  let url: URL;
  try {
    url = new URL(value, location.href);
  } catch {
    return null;
  }
  // The value goes back unchanged: Next.js finds its chunk scripts by their exact `src`.
  return url.origin === location.origin && url.pathname.startsWith("/_next/static/") ? value : null;
}

interface TrustedTypesFactory {
  createPolicy(name: string, rules: { createScriptURL?: (value: string) => string | null }): unknown;
  defaultPolicy?: unknown;
}

export function installDefaultPolicy(win: Window & { trustedTypes?: TrustedTypesFactory }): boolean {
  const factory = win.trustedTypes;
  if (!factory || factory.defaultPolicy) return false;
  factory.createPolicy("default", {
    createScriptURL: (value) => allowedScriptUrl(value, win.location),
  });
  return true;
}
