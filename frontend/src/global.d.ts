import type { formats } from "@/i18n/formats";
import type { IntlLocale } from "@/i18n/locales";
import type { Messages } from "@/messages";

declare module "next-intl" {
  interface AppConfig {
    Locale: IntlLocale;
    Messages: Messages;
    Formats: typeof formats;
  }
}
