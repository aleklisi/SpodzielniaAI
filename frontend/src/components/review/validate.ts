import type { Decision } from "@/lib/wiki";
import type { Messages } from "@/messages";

export interface DecisionDraft {
  decision: Decision | null;
  comment: string;
  supersedesId: string | null;
}

/** The decisions in their order; labels in `review.decision.<value>`. */
export const DECISIONS: Array<{ value: Decision; needsComment: boolean }> = [
  { value: "APPROVE", needsComment: false },
  { value: "REQUEST_CHANGES", needsComment: true },
  { value: "REJECT", needsComment: true },
  { value: "MARK_DISPUTED", needsComment: true },
];

export type DecisionError = keyof Messages["review"]["validation"];

/** Errors to show before sending (keys in `review.validation`); the server checks the same rules (and more). */
export function validateDecision(draft: DecisionDraft, articleId: string): DecisionError[] {
  const errors: DecisionError[] = [];
  if (!draft.decision) errors.push("chooseDecision");
  const rule = DECISIONS.find((d) => d.value === draft.decision);
  if (rule?.needsComment && draft.comment.trim().length < 10) errors.push("justify");
  if (draft.comment.length > 5000) errors.push("commentTooLong");
  if (draft.supersedesId && draft.decision !== "APPROVE") errors.push("supersedeNeedsApprove");
  if (draft.supersedesId === articleId) errors.push("supersedeSelf");
  return errors;
}
