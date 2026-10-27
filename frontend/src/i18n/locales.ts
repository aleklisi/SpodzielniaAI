/**
 * The UI language (Stage 14, US-08). The account and the `lang` cookie hold `pl` or `en` and
 * nothing else (rule R1); anything else reads as Polish. Formatting uses British English.
 */
export const LOCALES = ["pl", "en"] as const;
export type AppLocale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: AppLocale = "pl";
export const LANG_COOKIE = "lang";
export const TIME_ZONE = "Europe/Warsaw";

/** The locale next-intl and `Intl` use, and `<html lang>`. */
export const INTL_LOCALE = { pl: "pl", en: "en-GB" } as const satisfies Record<AppLocale, string>;
export type IntlLocale = (typeof INTL_LOCALE)[AppLocale];

/** Each language's own name, shown in the switch whatever the current language. */
export const LANGUAGE_NAMES: Record<AppLocale, string> = { pl: "Polski", en: "English" };

export function parseLocale(value: string | undefined | null): AppLocale | undefined {
  return (LOCALES as readonly string[]).includes(value ?? "") ? (value as AppLocale) : undefined;
}

export function appLocale(intl: string): AppLocale {
  return (Object.keys(INTL_LOCALE) as AppLocale[]).find((code) => INTL_LOCALE[code] === intl) ?? DEFAULT_LOCALE;
}

/** Render the page again in the new language (a reload: every server component re-renders). */
export function reloadPage(): void {
  window.location.reload();
}

/** A signed-out visitor's switch (signed in, the API sets it). Like the `sb` cookie (12.1). */
export function saveLocaleCookie(locale: AppLocale): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${LANG_COOKIE}=${locale}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}
