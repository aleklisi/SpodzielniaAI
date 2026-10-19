import { describe, expect, it, vi } from "vitest";

import {
  ChallengeRequired,
  makeClient,
  NotAuthenticated,
  RateLimited,
  readCookie,
  SecurityLocked,
  StepUpRequired,
} from "@/lib/api";

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function client(response: Response, cookies = "__Host-csrf=tok%3D123; other=1") {
  const fetch = vi.fn<(request: Request) => Promise<Response>>(async () => response.clone());
  const api = makeClient({ baseUrl: "https://spoldzielnia.localhost/api", fetch, cookies: () => cookies });
  return { api, fetch };
}

describe("CSRF", () => {
  it("echoes the CSRF cookie on unsafe methods only", async () => {
    const { api, fetch } = client(json(200, { answer_markdown: "", citations: [], request_id: "x" }));
    await api.POST("/rag/ask", { body: { question: "Jak leczyć warrozę?" } });
    await api.GET("/me");
    const [post, get] = fetch.mock.calls.map(([request]) => request);
    expect(post?.headers.get("X-CSRF-Token")).toBe("tok=123");
    expect(get?.headers.get("X-CSRF-Token")).toBeNull();
  });

  it("sends no header without the cookie", async () => {
    const { api, fetch } = client(json(200, {}), "");
    await api.POST("/auth/logout");
    expect(fetch.mock.calls[0]?.[0].headers.get("X-CSRF-Token")).toBeNull();
  });

  it("reads cookies", () => {
    expect(readCookie("b", "a=1; b=x%20y")).toBe("x y");
    expect(readCookie("c", "a=1")).toBeNull();
  });
});

describe("shield and auth responses", () => {
  const ask = (response: Response) =>
    client(response).api.POST("/rag/ask", { body: { question: "Jak leczyć warrozę?" } });

  it("maps a proof-of-work challenge", async () => {
    const error = await ask(
      json(401, { detail: { challenge: "pow", pow: { nonce: "ab", difficulty: 18, algorithm: "sha256" } } }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ChallengeRequired);
    expect((error as ChallengeRequired).challenge).toBe("pow");
    expect((error as ChallengeRequired).pow).toEqual({ nonce: "ab", difficulty: 18, algorithm: "sha256" });
  });

  it("maps an MFA step-up challenge with its login link", async () => {
    const error = await ask(
      json(401, { detail: { challenge: "mfa_step_up", pow: { nonce: "n", difficulty: 8 }, step_up: "/api/auth/login?prompt=login" } }),
    ).catch((e: unknown) => e);
    expect((error as ChallengeRequired).stepUp).toBe("/api/auth/login?prompt=login");
  });

  it("maps 401 without a challenge to NotAuthenticated", async () => {
    await expect(ask(json(401, { detail: "Not authenticated" }))).rejects.toBeInstanceOf(NotAuthenticated);
  });

  it("maps security locks (403 security_lock and 423)", async () => {
    await expect(ask(json(403, { detail: { error: "security_lock" } }))).rejects.toBeInstanceOf(SecurityLocked);
    await expect(ask(json(423, { detail: "Account locked" }))).rejects.toBeInstanceOf(SecurityLocked);
  });

  it("maps step-up for MFA-only routes", async () => {
    const error = await ask(json(403, { detail: { error: "mfa_required", step_up: "/api/auth/login" } })).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(StepUpRequired);
    expect((error as StepUpRequired).stepUp).toBe("/api/auth/login");
  });

  it("maps throttling with Retry-After", async () => {
    const error = await ask(json(429, { detail: { error: "rate_limited" } }, { "Retry-After": "1800" })).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(RateLimited);
    expect((error as RateLimited).retryAfterSeconds).toBe(1800);
  });

  it("leaves other errors to the caller", async () => {
    const { error, response } = await ask(json(422, { detail: [{ msg: "too short" }] }));
    expect(response.status).toBe(422);
    expect(error).toBeDefined();
  });
});
