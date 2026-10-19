/**
 * Security headers for every HTML response (6.1). Applied by `src/proxy.ts` with a fresh nonce
 * per request; Next.js reads the nonce from the request's CSP header and puts it on its own
 * scripts and styles. Caddy adds the static headers only where the app left them out.
 */

export function nonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

export function contentSecurityPolicy(n: string, dev = false): string {
  return [
    "default-src 'self'",
    // React needs eval in development only (error overlay, stack reconstruction).
    `script-src 'self' 'nonce-${n}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${n}'`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
    "require-trusted-types-for 'script'",
    "trusted-types dompurify nextjs#bundler default",
  ].join("; ");
}

export const STATIC_HEADERS: Readonly<Record<string, string>> = {
  "X-Frame-Options": "DENY",
  "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  // Microphone: voice notes in the notebook (6.2).
  "Permissions-Policy": "camera=(), microphone=(self), geolocation=()",
  "Cross-Origin-Opener-Policy": "same-origin",
};
