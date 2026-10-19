import { Badge, type Tone } from "./Badge";

/** Article statuses (4.2) and consent states (4.3) with their Polish labels. */
export const STATUS: Record<string, { label: string; tone: Tone }> = {
  DRAFT: { label: "Szkic", tone: "neutral" },
  IN_REVIEW: { label: "W recenzji", tone: "accent" },
  CANONICAL: { label: "Kanoniczny", tone: "ok" },
  SUPERSEDED: { label: "Zastąpiony", tone: "neutral" },
  DISPUTED: { label: "Sporny", tone: "warn" },
  REVISION_REQUIRED: { label: "Wymaga rewizji", tone: "warn" },
  REJECTED: { label: "Odrzucony", tone: "danger" },
  ACTIVE: { label: "Aktywny", tone: "ok" },
  SUSPENDED: { label: "Wstrzymany", tone: "warn" },
  REVOKED: { label: "Wycofany", tone: "danger" },
};

export function StatusPill({ status }: { status: string }) {
  const known = STATUS[status] ?? { label: status, tone: "neutral" as const };
  return <Badge tone={known.tone}>{known.label}</Badge>;
}
