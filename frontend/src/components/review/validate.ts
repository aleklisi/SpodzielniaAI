import type { Decision } from "@/lib/wiki";

export interface DecisionDraft {
  decision: Decision | null;
  comment: string;
  supersedesId: string | null;
}

export const DECISIONS: Array<{ value: Decision; label: string; needsComment: boolean }> = [
  { value: "APPROVE", label: "Zatwierdź (kanoniczny)", needsComment: false },
  { value: "REQUEST_CHANGES", label: "Poproś o zmiany", needsComment: true },
  { value: "REJECT", label: "Odrzuć", needsComment: true },
  { value: "MARK_DISPUTED", label: "Oznacz jako sporny", needsComment: true },
];

/** Errors to show before sending; the server checks the same rules (and more). */
export function validateDecision(draft: DecisionDraft, articleId: string): string[] {
  const errors: string[] = [];
  if (!draft.decision) errors.push("Wybierz decyzję.");
  const rule = DECISIONS.find((d) => d.value === draft.decision);
  if (rule?.needsComment && draft.comment.trim().length < 10) {
    errors.push("Uzasadnij decyzję (co najmniej 10 znaków): autor zobaczy ten komentarz.");
  }
  if (draft.comment.length > 5000) errors.push("Komentarz może mieć najwyżej 5000 znaków.");
  if (draft.supersedesId && draft.decision !== "APPROVE") errors.push("Zastąpić inny artykuł można tylko przy zatwierdzeniu.");
  if (draft.supersedesId === articleId) errors.push("Artykuł nie może zastąpić samego siebie.");
  return errors;
}
