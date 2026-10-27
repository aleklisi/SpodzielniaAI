"use client";

import { Globe } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";

import { cn } from "@/components/ui/cn";
import { appLocale, LANGUAGE_NAMES, LOCALES, reloadPage, saveLocaleCookie, type AppLocale } from "@/i18n/locales";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/session";

const SYNCED = "lang-synced";

/** Save the choice (the account, or the cookie when signed out), then reload the same page. */
async function switchTo(locale: AppLocale, signedIn: boolean): Promise<void> {
  if (signedIn) {
    const result = await api.PUT("/me/locale", { body: { locale } });
    if (!result.response.ok) throw new ApiError(result.response.status, result.error);
  }
  saveLocaleCookie(locale); // the API sets it too; this covers a signed-out visitor
  reloadPage();
}

/**
 * The UI language (14.2, US-08). Both names are shown in their own language. Choosing one saves
 * it and reloads the page, which renders in that language from the server on. In the icon rail
 * it is a globe button that opens the same choice.
 */
export function LanguageSwitch({ rail = false }: { rail?: boolean }) {
  const t = useTranslations("shell.language");
  const current = appLocale(useLocale());
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();

  // A choice made on another device: the account wins, once per tab (never a reload loop).
  const account = me.data?.locale_chosen ? me.data.locale : undefined;
  useEffect(() => {
    if (!account || account === current) return;
    try {
      if (sessionStorage.getItem(SYNCED) === account) return;
      sessionStorage.setItem(SYNCED, account);
    } catch {
      return; // no storage: keep the page as it is rather than risk reloading again
    }
    saveLocaleCookie(account);
    reloadPage();
  }, [account, current]);

  useEffect(() => {
    if (open) panel.current?.querySelector<HTMLInputElement>("input:checked")?.focus();
  }, [open]);

  // Until /me answers, it is unknown whether to save on the account or in the cookie alone.
  const unknown = me.isPending;

  async function choose(locale: AppLocale) {
    if (locale === current || busy || unknown) return;
    setBusy(true);
    setError(null);
    try {
      await switchTo(locale, Boolean(me.data));
    } catch {
      setError(t("failed"));
      setBusy(false);
    }
  }

  const group = (
    <fieldset aria-busy={busy || undefined} disabled={busy || unknown} className="flex flex-col gap-1">
      <legend className="px-1 pb-1 text-xs font-semibold tracking-wide text-text-muted uppercase">{t("label")}</legend>
      <div className="grid grid-cols-2 gap-1 rounded-lg bg-surface-muted p-1">
        {LOCALES.map((locale) => (
          <label
            key={locale}
            lang={locale}
            className={cn(
              "flex min-h-9 cursor-pointer items-center justify-center rounded-md px-2 text-sm pointer-coarse:min-h-11",
              "has-focus-visible:outline-2 has-focus-visible:outline-primary",
              locale === current ? "bg-surface font-medium text-text shadow-sm" : "text-text-muted hover:text-text",
            )}
          >
            <input
              type="radio"
              name={`${id}-lang`}
              value={locale}
              checked={locale === current}
              onChange={() => void choose(locale)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setOpen(false);
              }}
              className="sr-only"
            />
            {LANGUAGE_NAMES[locale]}
          </label>
        ))}
      </div>
      {error && (
        <p role="alert" className="px-1 text-xs text-danger">
          {error}
        </p>
      )}
    </fieldset>
  );

  if (!rail) return <div className="px-1 py-1">{group}</div>;
  return (
    <div className="relative flex justify-center">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        aria-label={`${t("label")}: ${LANGUAGE_NAMES[current]}`}
        onClick={() => setOpen(!open)}
        className="inline-flex size-10 items-center justify-center rounded-lg text-text-muted hover:bg-sidebar-accent hover:text-text pointer-coarse:size-11"
      >
        <Globe aria-hidden className="size-5" />
      </button>
      {open && (
        <div
          ref={panel}
          id={`${id}-panel`}
          onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
          }}
          className="absolute bottom-0 left-full z-50 ml-3 w-48 rounded-lg border border-border bg-surface p-2 shadow-lg"
        >
          {group}
        </div>
      )}
    </div>
  );
}
