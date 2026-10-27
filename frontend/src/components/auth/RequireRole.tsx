"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Card } from "@/components/ui/Card";
import { hasRole, useMe, type Role } from "@/lib/session";

/**
 * Hides a screen from accounts without `role`. Convenience only: the API enforces the role
 * (and MFA) on every request, so a forged client state would still get 403s.
 */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const t = useTranslations("common");
  const me = useMe();
  if (me.isPending) return <p className="text-text-muted">{t("loading")}</p>;
  if (!hasRole(me.data, role)) {
    return (
      <Card role="alert" className="max-w-xl">
        <h1 className="font-display text-xl font-semibold">{t("access.deniedTitle")}</h1>
        <p className="mt-1 text-sm text-text-muted">{t("access.deniedBody")}</p>
      </Card>
    );
  }
  return <>{children}</>;
}

/** The API wants a fresh login with a second factor (403 mfa_required / reauthentication). */
export function StepUpNotice({ stepUp, reason }: { stepUp: string; reason?: string }) {
  const t = useTranslations("common.access");
  const returnTo = typeof window === "undefined" ? "/" : window.location.pathname;
  const href = `${stepUp}${stepUp.includes("?") ? "&" : "?"}return_to=${encodeURIComponent(returnTo)}`;
  return (
    <Card role="alert" className="max-w-xl border-warn">
      <h2 className="font-display text-lg font-semibold">{t("stepUpTitle")}</h2>
      <p className="mt-1 text-sm">
        {reason ?? t("stepUpBody")}
      </p>
      <a href={href} className="mt-3 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-contrast">
        {t("stepUpAction")}
      </a>
    </Card>
  );
}
