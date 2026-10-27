/**
 * `@testing-library/react` for Vitest (aliased in vitest.config.mts): every render and renderHook
 * gets the message catalogue (14.2), Polish unless a test calls `setTestLocale("en")`.
 */
import * as rtl from "@testing-library/react/pure";
import { NextIntlClientProvider } from "next-intl";
import type { ComponentType, ReactNode } from "react";
import { afterEach } from "vitest";

import { formats } from "@/i18n/formats";
import { INTL_LOCALE, TIME_ZONE, type AppLocale } from "@/i18n/locales";
import { MESSAGES } from "@/messages";

export * from "@testing-library/react/pure";

let locale: AppLocale = "pl";

export function setTestLocale(next: AppLocale): void {
  locale = next;
}

afterEach(() => {
  locale = "pl";
});

function withIntl(Inner?: ComponentType<{ children: ReactNode }>) {
  return function IntlWrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale={INTL_LOCALE[locale]} messages={MESSAGES[locale]} timeZone={TIME_ZONE} formats={formats}>
        {Inner ? <Inner>{children}</Inner> : children}
      </NextIntlClientProvider>
    );
  };
}

export const render: typeof rtl.render = ((ui: Parameters<typeof rtl.render>[0], options?: rtl.RenderOptions) =>
  rtl.render(ui, { ...options, wrapper: withIntl(options?.wrapper as ComponentType<{ children: ReactNode }> | undefined) })) as typeof rtl.render;

export const renderHook: typeof rtl.renderHook = ((callback: (props: unknown) => unknown, options?: rtl.RenderHookOptions<unknown>) =>
  rtl.renderHook(callback, {
    ...options,
    wrapper: withIntl(options?.wrapper as ComponentType<{ children: ReactNode }> | undefined),
  })) as typeof rtl.renderHook;
