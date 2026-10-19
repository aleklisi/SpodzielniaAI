/**
 * Typed client for the FastAPI backend (same origin, `/api`, via Caddy). Types come from the
 * backend's OpenAPI schema: `npm run gen:api` regenerates `api-schema.d.ts`.
 *
 * - Unsafe methods carry `X-CSRF-Token`, echoed from the `__Host-csrf` cookie (double submit,
 *   backend 1.6). The session cookie itself is HttpOnly and never touched here.
 * - Shield and auth responses become typed errors, so screens can react to them:
 *   `ChallengeRequired` (401 with `challenge`, 5.6), `StepUpRequired` (403 mfa_required /
 *   reauthentication_required), `SecurityLocked` (403 security_lock, 423), `RateLimited`
 *   (429 + Retry-After), `NotAuthenticated` (401 otherwise).
 */

import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "@/lib/api-schema";

export const API_BASE = "/api";
export const CSRF_COOKIE = "__Host-csrf";
export const CSRF_HEADER = "X-CSRF-Token";
export const POW_HEADER = "X-Shield-PoW";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export interface PowPuzzle {
  nonce: string;
  difficulty: number;
  algorithm: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`API ${status}`);
    this.name = new.target.name;
  }
}

export class NotAuthenticated extends ApiError {}

export class ChallengeRequired extends ApiError {
  constructor(
    status: number,
    detail: unknown,
    readonly challenge: "pow" | "mfa_step_up",
    readonly pow: PowPuzzle | null,
    readonly stepUp: string | null,
  ) {
    super(status, detail);
  }
}

export class StepUpRequired extends ApiError {
  constructor(
    status: number,
    detail: unknown,
    readonly stepUp: string,
  ) {
    super(status, detail);
  }
}

export class SecurityLocked extends ApiError {}

export class RateLimited extends ApiError {
  constructor(
    status: number,
    detail: unknown,
    readonly retryAfterSeconds: number,
  ) {
    super(status, detail);
  }
}

export function readCookie(name: string, cookies: string): string | null {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export function loginUrl(returnTo: string): string {
  return `${API_BASE}/auth/login?return_to=${encodeURIComponent(returnTo)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** The error a response stands for, or null if it is an ordinary one. */
export function errorFor(status: number, body: unknown, headers: Headers): ApiError | null {
  const detail = isRecord(body) ? body.detail : undefined;
  const fields = isRecord(detail) ? detail : {};
  if (status === 401) {
    const challenge = fields.challenge;
    if (challenge === "pow" || challenge === "mfa_step_up") {
      const pow = isRecord(fields.pow) ? (fields.pow as unknown as PowPuzzle) : null;
      const stepUp = typeof fields.step_up === "string" ? fields.step_up : null;
      return new ChallengeRequired(status, detail, challenge, pow, stepUp);
    }
    return new NotAuthenticated(status, detail);
  }
  if (status === 423 || (status === 403 && fields.error === "security_lock")) {
    return new SecurityLocked(status, detail);
  }
  if (status === 403 && typeof fields.step_up === "string") {
    return new StepUpRequired(status, detail, fields.step_up);
  }
  if (status === 429) {
    const retry = Number(headers.get("Retry-After") ?? "60");
    return new RateLimited(status, detail, Number.isFinite(retry) ? retry : 60);
  }
  return null;
}

export const csrfMiddleware = (cookies: () => string): Middleware => ({
  onRequest({ request }) {
    if (SAFE_METHODS.has(request.method.toUpperCase())) return request;
    const token = readCookie(CSRF_COOKIE, cookies());
    if (token) request.headers.set(CSRF_HEADER, token);
    return request;
  },
});

export const shieldMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return response;
    const body: unknown = await response
      .clone()
      .json()
      .catch(() => null);
    const error = errorFor(response.status, body, response.headers);
    if (error) throw error;
    return response;
  },
};

export interface ClientOptions {
  baseUrl?: string;
  fetch?: (request: Request) => Promise<Response>;
  cookies?: () => string;
}

export function makeClient(options: ClientOptions = {}) {
  // Same origin, as an absolute URL where there is a window (Node's Request needs one).
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? `${origin}${API_BASE}`,
    // Looked up per call, not captured at import: test doubles (MSW) patch it later.
    fetch: options.fetch ?? ((request: Request) => globalThis.fetch(request)),
    credentials: "same-origin",
  });
  const cookies = options.cookies ?? (() => (typeof document === "undefined" ? "" : document.cookie));
  client.use(csrfMiddleware(cookies));
  client.use(shieldMiddleware);
  return client;
}

export const api = makeClient();
