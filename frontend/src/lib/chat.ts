/** Asking the cooperative (6.5, backend 5.6): what can come back besides an answer. */

import { api, ChallengeRequired, POW_HEADER, RateLimited, SecurityLocked, type PowPuzzle } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type RagAnswer = components["schemas"]["RagAnswer"];
export type Citation = components["schemas"]["Citation"];

export type AskOutcome =
  | { kind: "answer"; answer: RagAnswer }
  | { kind: "pow"; puzzle: PowPuzzle }
  | { kind: "mfa"; stepUp: string }
  | { kind: "locked" }
  | { kind: "throttled"; retryAfterSeconds: number };

export type Ask = (question: string, powSolution?: string) => Promise<AskOutcome>;

export const askCooperative: Ask = async (question, powSolution) => {
  try {
    const result = await api.POST("/rag/ask", {
      body: { question },
      headers: powSolution ? { [POW_HEADER]: powSolution } : undefined,
    });
    return { kind: "answer", answer: unwrap(result) };
  } catch (error) {
    if (error instanceof ChallengeRequired) {
      if (error.challenge === "mfa_step_up" && error.stepUp) return { kind: "mfa", stepUp: error.stepUp };
      if (error.pow) return { kind: "pow", puzzle: error.pow };
    }
    if (error instanceof SecurityLocked) return { kind: "locked" };
    if (error instanceof RateLimited) return { kind: "throttled", retryAfterSeconds: error.retryAfterSeconds };
    throw error;
  }
};
