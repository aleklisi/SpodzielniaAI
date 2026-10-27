import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import { MESSAGES } from "@/messages";

import { formats } from "./formats";
import { DEFAULT_LOCALE, INTL_LOCALE, LANG_COOKIE, parseLocale, TIME_ZONE } from "./locales";

/**
 * The request's language: the `lang` cookie, which the API sets at sign-in from the account and
 * on every change (14.1). No URL prefix and no `Accept-Language`: Polish until the user chooses.
 */
export default getRequestConfig(async () => {
  const locale = parseLocale((await cookies()).get(LANG_COOKIE)?.value) ?? DEFAULT_LOCALE;
  return { locale: INTL_LOCALE[locale], messages: MESSAGES[locale], timeZone: TIME_ZONE, formats };
});
