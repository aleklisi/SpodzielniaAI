"use client";

import { useQueryClient } from "@tanstack/react-query";
import { LogIn, LogOut, PanelLeftClose, PanelLeftOpen, Plus, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { cn } from "@/components/ui/cn";
import { api, ApiError, loginUrl } from "@/lib/api";
import { useBranding } from "@/lib/branding-context";
import { useCreateNote } from "@/lib/notebook";
import { useMe } from "@/lib/session";

import { BrandMark } from "./BrandMark";
import { LanguageSwitch } from "./LanguageSwitch";
import { activeHref, PRIVACY_ITEM, visibleSections, type NavItem } from "./nav-items";

export type SidebarMode = "desktop" | "drawer";

/**
 * The menu (12.1). On the desktop it is 16rem wide or a 4rem icon rail (`collapsed`); between md
 * and lg it is always the rail and "Expand menu" opens the drawer over the workspace. In the
 * drawer it always shows its labels. `sb-wide:` (globals.css) styles the labelled state.
 */
export function Sidebar({
  mode,
  collapsed = false,
  onToggle,
  onExpand,
  onClose,
}: {
  mode: SidebarMode;
  collapsed?: boolean;
  /** lg+: collapse to the rail or expand back. */
  onToggle?: () => void;
  /** md–lg: open the full menu over the workspace. */
  onExpand?: () => void;
  /** The drawer: close it (also after a link or an action is used). */
  onClose?: () => void;
}) {
  const t = useTranslations("shell");
  const me = useMe();
  const path = usePathname();
  const { org_name, logo_url } = useBranding();
  const sections = visibleSections(me.data);
  const hrefs = [...sections.flatMap((s) => s.items), PRIVACY_ITEM].map((i) => i.href);
  const current = activeHref(path, hrefs);
  const signedOut = me.error instanceof ApiError && me.error.status === 401;
  const state = mode === "drawer" ? "drawer" : collapsed ? "collapsed" : "expanded";

  return (
    <aside
      data-sidebar={state}
      className={cn(
        "flex h-full flex-col bg-sidebar text-text",
        mode === "desktop" && "w-16 border-r border-border transition-[width] duration-200 sb-wide:w-64",
        mode === "drawer" && "w-full",
      )}
    >
      <div className="flex flex-col items-center gap-2 px-3 py-4 sb-wide:flex-row sb-wide:justify-between">
        <Link
          href="/"
          onClick={onClose}
          className="group/link relative flex min-w-0 items-center gap-2 rounded-lg font-display text-base font-semibold text-primary-strong"
        >
          <BrandMark rail />
          {/* With a logo, the name gets its own line below (it would not fit beside the toggle). */}
          <span className={cn("truncate", logo_url ? "sr-only" : "sr-only sb-wide:not-sr-only")}>{org_name}</span>
          <Tip>{org_name}</Tip>
        </Link>
        {mode === "desktop" && (
          <>
            {/* Wrappers carry the breakpoint: `cn` does not resolve conflicting display classes. */}
            <span className="hidden lg:contents">
              <IconButton
                label={collapsed ? t("expandMenu") : t("collapseMenu")}
                icon={collapsed ? PanelLeftOpen : PanelLeftClose}
                onClick={onToggle}
              />
            </span>
            <span className="contents lg:hidden">
              <IconButton label={t("expandMenu")} icon={PanelLeftOpen} onClick={onExpand} />
            </span>
          </>
        )}
        {mode === "drawer" && <IconButton label={t("closeMenu")} icon={X} onClick={onClose} />}
      </div>
      {logo_url && (
        <p aria-hidden className="-mt-2 hidden truncate px-4 pb-2 font-display text-sm font-semibold text-primary-strong sb-wide:block">
          {org_name}
        </p>
      )}

      {me.data && <CreateNote onDone={onClose} />}

      <nav aria-label={t("mainNav")} className="flex-1 px-2 pb-2 sb-wide:overflow-y-auto">
        {sections.map((section, index) => (
          <div key={section.key}>
            <p className="hidden px-3 pt-4 pb-1 text-xs font-semibold tracking-wide text-text-muted uppercase sb-wide:block">
              {t(`sections.${section.key}`)}
            </p>
            {index > 0 && <hr aria-hidden className="mx-2 my-2 border-border sb-wide:hidden" />}
            <ul aria-label={t(`sections.${section.key}`)} className="flex flex-col gap-0.5">
              {section.items.map((item) => (
                <li key={item.href}>
                  <NavLink item={item} active={current === item.href} onNavigate={onClose} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="flex flex-col gap-0.5 border-t border-border px-2 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        {me.data && <NavLink item={PRIVACY_ITEM} active={current === PRIVACY_ITEM.href} onNavigate={onClose} />}
        {mode === "drawer" ? (
          <LanguageSwitch />
        ) : (
          <>
            {/* The rail offers the choice behind a globe button; the labelled menu shows it. */}
            <div className="sb-wide:hidden">
              <LanguageSwitch rail />
            </div>
            <div className="hidden sb-wide:block">
              <LanguageSwitch />
            </div>
          </>
        )}
        {me.data && <Account name={me.data.display_name ?? me.data.email ?? t("account")} onDone={onClose} />}
        {signedOut && (
          <a href={loginUrl(path)} className={cn(ITEM, "font-medium text-primary-strong")}>
            <LogIn aria-hidden className="size-5 shrink-0" />
            <span className="sr-only sb-wide:not-sr-only">{t("signIn")}</span>
            <Tip>{t("signIn")}</Tip>
          </a>
        )}
      </div>
    </aside>
  );
}

const ITEM =
  "group/link relative flex min-h-10 items-center justify-center gap-3 rounded-lg px-3 py-2 text-sm pointer-coarse:min-h-11 hover:bg-sidebar-accent sb-wide:justify-start";

function NavLink({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate?: () => void }) {
  const t = useTranslations("shell.nav");
  const Icon = item.icon;
  const label = t(item.key);
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(ITEM, active ? "bg-sidebar-accent font-medium text-text" : "text-text-muted hover:text-text")}
    >
      <Icon aria-hidden className={cn("size-5 shrink-0", active && "text-primary-strong")} />
      <span className="sr-only truncate sb-wide:not-sr-only">{label}</span>
      <Tip>{label}</Tip>
    </Link>
  );
}

function CreateNote({ onDone }: { onDone?: () => void }) {
  const t = useTranslations("shell");
  const create = useCreateNote();
  const router = useRouter();
  return (
    <div className="px-2 pb-2">
      <button
        type="button"
        disabled={create.isPending}
        aria-busy={create.isPending || undefined}
        onClick={async () => {
          const note = await create.mutateAsync(t("newNoteTitle"));
          onDone?.();
          router.push(`/notebook/${note.id}`);
        }}
        className="group/link relative flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-contrast hover:bg-primary-strong disabled:opacity-50 pointer-coarse:min-h-11"
      >
        <Plus aria-hidden className="size-5 shrink-0" />
        <span className="sr-only sb-wide:not-sr-only">{t("createNote")}</span>
        <Tip>{t("createNote")}</Tip>
      </button>
    </div>
  );
}

function Account({ name, onDone }: { name: string; onDone?: () => void }) {
  const t = useTranslations("shell");
  const router = useRouter();
  const client = useQueryClient();
  return (
    <div className="flex flex-col items-center gap-1 sb-wide:flex-row sb-wide:gap-3 sb-wide:px-3 sb-wide:py-1">
      <span
        aria-hidden
        className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-sm font-semibold text-primary-strong"
      >
        {name.charAt(0).toUpperCase()}
      </span>
      <span className="sr-only min-w-0 flex-1 truncate text-sm sb-wide:not-sr-only">{name}</span>
      <IconButton
        label={t("signOut")}
        icon={LogOut}
        onClick={async () => {
          await api.POST("/auth/logout").catch(() => undefined);
          client.clear(); // nothing of the previous account stays in memory
          onDone?.();
          router.push("/");
        }}
      />
    </div>
  );
}

function IconButton({
  label,
  icon: Icon,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="group/link relative inline-flex size-10 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-sidebar-accent hover:text-text pointer-coarse:size-11"
    >
      <Icon aria-hidden className="size-5" />
      <Tip>{label}</Tip>
    </button>
  );
}

/** The rail's tooltip: the label already names the control, so it stays out of the a11y tree. */
function Tip({ children }: { children: ReactNode }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-full z-50 ml-3 hidden rounded-md bg-text px-2 py-1 text-xs whitespace-nowrap text-bg shadow-md group-hover/link:block group-focus-visible/link:block sb-wide:hidden!"
    >
      {children}
    </span>
  );
}
