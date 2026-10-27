"use client";

import { LogIn } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { visibleSections } from "@/components/shell/nav-items";
import { Card } from "@/components/ui/Card";
import { Loading } from "@/components/ui/Skeleton";
import { ApiError, loginUrl } from "@/lib/api";
import { useBranding } from "@/lib/branding-context";
import { useMe } from "@/lib/session";



/** The start page: the menu's sections as cards, for the account's roles (12.5). */
export function HomeDashboard() {
  const t = useTranslations("home");
  const nav = useTranslations("shell");
  const me = useMe();
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  const sections = visibleSections(me.data);
  const name = me.data?.display_name ?? me.data?.email;
  const { org_name } = useBranding();
  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-2">
        <h1 className="font-display text-3xl font-semibold">{name ? t("greeting", { name }) : org_name}</h1>
        <p className="max-w-2xl text-text-muted">{t("tagline")}</p>
        {signedOut && (
          <a
            href={loginUrl("/")}
            className="mt-2 inline-flex w-fit items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-strong pointer-coarse:min-h-11"
          >
            <LogIn aria-hidden className="size-4" />
            {nav("signIn")}
          </a>
        )}
      </section>
      {me.isPending && <Loading rows={2} />}
      {sections.map((section) => (
        <section key={section.key} aria-label={nav(`sections.${section.key}`)} className="flex flex-col gap-3">
          <h2 className="text-xs font-semibold tracking-wide text-text-muted uppercase">{nav(`sections.${section.key}`)}</h2>
          <ul className="grid gap-3 @2xl:grid-cols-2 @5xl:grid-cols-3">
            {section.items.map(({ href, key, icon: Icon }) => (
              <li key={href}>
                <Link href={href} className="block h-full">
                  <Card className="flex h-full gap-4 transition-colors hover:border-primary">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary-strong">
                      <Icon aria-hidden className="size-5" />
                    </span>
                    <span className="flex flex-col gap-1">
                      <span className="font-display text-lg font-semibold">{nav(`nav.${key}`)}</span>
                      <span className="text-sm text-text-muted">{key !== "privacy" && t(`cards.${key}`)}</span>
                    </span>
                  </Card>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
