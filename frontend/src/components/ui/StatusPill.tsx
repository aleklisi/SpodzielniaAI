"use client";

import { useTranslations } from "next-intl";

import type { Messages } from "@/messages";

import { Badge, type Tone } from "./Badge";

type Status = keyof Messages["common"]["status"];

/** Article statuses (4.2) and consent states (4.3); labels in `common.status`. */
export const STATUS_TONE: Record<Status, Tone> = {
  DRAFT: "neutral",
  IN_REVIEW: "accent",
  CANONICAL: "ok",
  SUPERSEDED: "neutral",
  DISPUTED: "warn",
  REVISION_REQUIRED: "warn",
  REJECTED: "danger",
  ACTIVE: "ok",
  SUSPENDED: "warn",
  REVOKED: "danger",
};

export function isStatus(status: string): status is Status {
  return status in STATUS_TONE;
}

export function StatusPill({ status }: { status: string }) {
  const t = useTranslations("common.status");
  if (!isStatus(status)) return <Badge tone="neutral">{status}</Badge>;
  return <Badge tone={STATUS_TONE[status]}>{t(status)}</Badge>;
}
