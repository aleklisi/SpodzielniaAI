"use client";

import { ImageUp, RotateCcw, Save, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  brandingErrorKey,
  type BrandingErrorKey,
  useAdminBranding,
  usePreview,
  useRemoveLogo,
  useResetBranding,
  useSaveBranding,
  useUploadLogo,
  type AdminBranding,
  type Preview,
} from "@/lib/branding-admin";
import { DEFAULT_NAME, TOKENS } from "@/lib/branding-css";

// The fixed surfaces of globals.css, so each preview panel shows its own mode whatever the system's.
const SURFACES = {
  light: { bg: "#fbf8f1", surface: "#ffffff", "surface-muted": "#f3eee2", text: "#241f16", "text-muted": "#6b6354", border: "#e3dccb" },
  dark: { bg: "#15130f", surface: "#1f1c16", "surface-muted": "#29251d", text: "#f2ede2", "text-muted": "#b3aa98", border: "#3a342a" },
} as const;

type Mode = keyof typeof SURFACES;

// The default (honey) tokens of globals.css: each panel sets every token of its own mode, so a
// dark panel never inherits the page's light colours.
const DEFAULT_TOKENS: Record<Mode, Record<(typeof TOKENS)[number], string>> = {
  light: {
    primary: "#9a6414",
    "primary-strong": "#7a4d0d",
    "primary-contrast": "#ffffff",
    accent: "#6b4f96",
    sidebar: "#f6f1e5",
    "sidebar-accent": "#ece4d1",
  },
  dark: {
    primary: "#e0a43a",
    "primary-strong": "#f0bd5e",
    "primary-contrast": "#1a1405",
    accent: "#b49ae0",
    sidebar: "#1a1813",
    "sidebar-accent": "#2e2920",
  },
};

const HEX = /^#[0-9a-f]{6}$/i;
const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm";

const TOKEN_LABELS = ["primary", "primary-contrast", "primary-strong", "accent", "sidebar-accent"] as const;
const AGAINST_LABELS = ["primary", "bg", "surfaces", "text-muted"] as const;

function known<K extends string>(list: readonly K[], value: string): value is K {
  return (list as readonly string[]).includes(value);
}

/** "Appearance" (13.5): the organisation's name, logo and two colours, with a live preview. */
export function BrandingConsole() {
  const t = useTranslations("adminBranding");
  const branding = useAdminBranding();
  if (branding.error instanceof StepUpRequired) return <StepUpNotice stepUp={branding.error.stepUp} />;
  if (branding.isPending) return <Loading />;
  if (!branding.data) {
    return (
      <Card role="alert" className="max-w-xl">
        {t("loadFailed")}
      </Card>
    );
  }
  // Not keyed by version: a logo change must not discard unsaved name and colour edits.
  return <Console current={branding.data} />;
}

function Console({ current }: { current: AdminBranding }) {
  const t = useTranslations("adminBranding");
  const common = useTranslations("common");
  const toast = useToast();
  const save = useSaveBranding();
  const reset = useResetBranding();
  const [name, setName] = useState(current.custom_name ? current.org_name : "");
  const [custom, setCustom] = useState(current.brand !== null);
  const [brand, setBrand] = useState(current.brand ?? "#9a6414");
  const [accent, setAccent] = useState(current.accent ?? "#6b4f96");
  const [confirmReset, setConfirmReset] = useState(false);
  const preview = useLivePreview(custom, brand, accent);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await save.mutateAsync({
        org_name: name.trim() || null,
        brand: custom ? brand.toLowerCase() : null,
        accent: custom ? accent.toLowerCase() : null,
      });
      toast(t("saved"), "ok");
    } catch (error) {
      toast(t(`errors.${brandingErrorKey(error)}`), "error");
    }
  }

  const shownName = name.trim() || DEFAULT_NAME;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button variant="ghost" className="text-danger" onClick={() => setConfirmReset(true)}>
            <RotateCcw aria-hidden className="size-4" />
            {t("reset")}
          </Button>
        }
      />
      <div className="grid gap-6 @4xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-6">
          <Card>
            <form onSubmit={submit} className="flex flex-col gap-4 text-sm" aria-label={t("nameAndColours")}>
              <h2 className="font-display text-lg font-semibold">{t("nameAndColours")}</h2>
              <div className="flex flex-col gap-1">
                <label htmlFor="org-name" className="font-medium">
                  {t("orgName")}
                </label>
                <input
                  id="org-name"
                  aria-describedby="org-name-hint"
                  className={INPUT}
                  value={name}
                  maxLength={60}
                  placeholder={DEFAULT_NAME}
                  onChange={(e) => setName(e.target.value)}
                />
                <span id="org-name-hint" className="text-xs text-text-muted">
                  {t("orgNameHint", { name: DEFAULT_NAME })}
                </span>
              </div>
              <label className="flex items-center gap-2">
                <input type="checkbox" className="size-4 accent-primary" checked={custom} onChange={(e) => setCustom(e.target.checked)} />
                {t("customColours")}
              </label>
              {custom && (
                <div className="grid gap-4 @lg:grid-cols-2">
                  <ColourField id="brand-colour" label={t("brand")} hint={t("brandHint")} value={brand} onChange={setBrand} />
                  <ColourField id="accent-colour" label={t("accent")} hint={t("accentHint")} value={accent} onChange={setAccent} />
                </div>
              )}
              {preview.error && (
                <p role="alert" className="text-danger">
                  {t(`errors.${preview.error}`)}
                </p>
              )}
              {preview.data && preview.data.adjusted.length > 0 && (
                <p role="status" className="rounded-lg bg-surface-muted px-3 py-2">
                  {preview.data.adjusted.includes("brand") && t("brandAdjusted")}
                  {preview.data.adjusted.includes("accent") && t("accentAdjusted")}
                </p>
              )}
              <div>
                <Button type="submit" busy={save.isPending} disabled={custom && (!HEX.test(brand) || !HEX.test(accent) || !!preview.error)}>
                  <Save aria-hidden className="size-4" />
                  {common("save")}
                </Button>
              </div>
            </form>
          </Card>
          <LogoCard current={current} />
        </div>
        <section aria-label={t("preview")} className="flex flex-col gap-4">
          <h2 className="font-display text-lg font-semibold">{t("preview")}</h2>
          <div className="grid gap-4 @lg:grid-cols-2 @4xl:grid-cols-1 @6xl:grid-cols-2">
            {(["light", "dark"] as const).map((mode) => (
              <PreviewPanel
                key={mode}
                mode={mode}
                name={shownName}
                logo={current.logo_url}
                // While the server's preview loads, the saved colours; without own colours, the defaults.
                tokens={custom ? (preview.data?.[mode] ?? current.palette?.[mode] ?? null) : null}
              />
            ))}
          </div>
          {custom && preview.data && <Ratios preview={preview.data} />}
        </section>
      </div>
      <Dialog open={confirmReset} onClose={() => setConfirmReset(false)} title={t("resetTitle")}>
        <p className="text-sm">{t("resetBody", { name: DEFAULT_NAME })}</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmReset(false)}>
            {common("cancel")}
          </Button>
          <Button
            variant="danger"
            busy={reset.isPending}
            onClick={async () => {
              try {
                await reset.mutateAsync();
                setName("");
                setCustom(false);
                setConfirmReset(false);
                toast(t("resetDone"), "ok");
              } catch (error) {
                toast(t(`errors.${brandingErrorKey(error)}`), "error");
              }
            }}
          >
            {t("resetSubmit")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

/** The server's palette for the chosen colours, asked again 300 ms after the last change. */
function useLivePreview(enabled: boolean, brand: string, accent: string): { data: Preview | null; error: BrandingErrorKey | null } {
  const { mutate } = usePreview();
  const [state, setState] = useState<{ key: string; data: Preview | null; error: BrandingErrorKey | null }>({ key: "", data: null, error: null });
  const key = `${brand.toLowerCase()}|${accent.toLowerCase()}`;
  const valid = enabled && HEX.test(brand) && HEX.test(accent);
  useEffect(() => {
    if (!valid) return;
    const timer = window.setTimeout(() => {
      mutate(
        { brand: brand.toLowerCase(), accent: accent.toLowerCase() },
        {
          onSuccess: (data) => setState({ key, data, error: null }),
          onError: (error) => setState({ key, data: null, error: brandingErrorKey(error) }),
        },
      );
    }, 300);
    return () => window.clearTimeout(timer);
  }, [valid, key, brand, accent, mutate]);
  if (!enabled) return { data: null, error: null };
  if (!HEX.test(brand) || !HEX.test(accent)) return { data: null, error: "invalid_colour" };
  return state.key === key ? state : { data: null, error: null };
}

function ColourField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const t = useTranslations("adminBranding");
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={t("picker", { label })}
          value={HEX.test(value) ? value.toLowerCase() : "#000000"}
          onChange={(e) => onChange(e.target.value)}
          className="h-10 w-12 shrink-0 cursor-pointer rounded-lg border border-border bg-surface p-1"
        />
        <input id={id} className={`${INPUT} font-mono`} value={value} maxLength={7} spellCheck={false} onChange={(e) => onChange(e.target.value.trim())} />
      </div>
      <span className="text-xs text-text-muted">{hint}</span>
    </div>
  );
}

/**
 * One mode of the look: a menu strip, a button, a link, a badge and a card in the preview's
 * colours. The colours are set on this element through the CSSOM (`style.setProperty`), which
 * the CSP allows; there is no `style=` attribute in the HTML.
 */
function PreviewPanel({
  mode,
  name,
  logo,
  tokens,
}: {
  mode: Mode;
  name: string;
  logo: string | null;
  tokens: Record<string, string> | null;
}) {
  const t = useTranslations("adminBranding");
  const nav = useTranslations("shell.nav");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    for (const [token, value] of Object.entries(SURFACES[mode])) element.style.setProperty(`--${token}`, value);
    for (const token of TOKENS) {
      const value = tokens?.[token];
      element.style.setProperty(`--${token}`, value && /^#[0-9a-f]{6}$/.test(value) ? value : DEFAULT_TOKENS[mode][token]);
    }
  }, [mode, tokens]);
  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="text-xs font-medium text-text-muted">{mode === "light" ? t("light") : t("dark")}</figcaption>
      <div
        ref={ref}
        data-testid={`preview-${mode}`}
        className="flex overflow-hidden rounded-xl border border-border bg-bg text-text"
      >
        <div className="flex w-28 shrink-0 flex-col gap-1 bg-sidebar p-2 text-xs">
          <span className="mb-1 flex min-w-0 items-center gap-1 font-display font-semibold text-primary-strong">
            {/* eslint-disable-next-line @next/next/no-img-element -- the current same-origin logo */}
            {logo && <img src={logo} alt="" className="h-4 w-auto max-w-10 object-contain" />}
            <span className="truncate">{name}</span>
          </span>
          <span className="rounded bg-sidebar-accent px-2 py-1 font-medium">{nav("notebook")}</span>
          <span className="px-2 py-1 text-text-muted">{nav("wiki")}</span>
          <span className="px-2 py-1 text-text-muted">{nav("atlas")}</span>
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2 p-3 text-xs">
          <span className="font-display text-sm font-semibold">{t("sample.heading")}</span>
          <div className="rounded-lg border border-border bg-surface p-2">
            {t.rich("sample.card", { link: (chunks) => <span className="font-medium text-primary-strong underline">{chunks}</span> })}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-lg bg-primary px-2 py-1 font-medium text-primary-contrast">{t("sample.button")}</span>
            <Badge tone="primary">{t("sample.badge")}</Badge>
            <Badge tone="accent">{t("sample.accent")}</Badge>
          </div>
        </div>
      </div>
    </figure>
  );
}

function Ratios({ preview }: { preview: Preview }) {
  const t = useTranslations("adminBranding");
  return (
    <details className="text-sm">
      <summary className="cursor-pointer font-medium">{t("contrast")}</summary>
      <ul className="mt-2 flex flex-col gap-1" aria-label={t("contrastList")}>
        {preview.checks.map((c) => (
          <li key={`${c.mode}-${c.token}-${c.against}`} className="flex flex-wrap justify-between gap-2 border-b border-border pb-1 last:border-0">
            <span>
              {c.mode === "light" ? t("lightShort") : t("darkShort")} · {known(TOKEN_LABELS, c.token) ? t(`tokens.${c.token}`) : c.token} /{" "}
              {known(AGAINST_LABELS, c.against) ? t(`against.${c.against}`) : c.against}
            </span>
            <span className={c.ratio >= c.required ? "text-ok" : "text-danger"}>
              {t("minimum", { ratio: c.ratio.toFixed(2), required: c.required })}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function LogoCard({ current }: { current: AdminBranding }) {
  const t = useTranslations("adminBranding");
  const upload = useUploadLogo();
  const remove = useRemoveLogo();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  return (
    <Card className="flex flex-col gap-3 text-sm">
      <h2 className="font-display text-lg font-semibold">{t("logo.title")}</h2>
      <div className="flex min-h-20 items-center justify-center rounded-lg border border-dashed border-border bg-surface-muted p-3">
        {current.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- the current same-origin logo
          <img src={current.logo_url} alt={t("logo.alt", { name: current.org_name })} className="max-h-16 w-auto object-contain" />
        ) : (
          <span className="text-text-muted">{t("logo.none")}</span>
        )}
      </div>
      <p className="text-xs text-text-muted">{t("logo.hint")}</p>
      <label className="sr-only" htmlFor="logo-file">
        {t("logo.file")}
      </label>
      <input
        ref={input}
        id="logo-file"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="sr-only"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file) return;
          try {
            await upload.mutateAsync(file);
            toast(t("logo.saved"), "ok");
          } catch (error) {
            toast(t(`errors.${brandingErrorKey(error)}`), "error");
          }
        }}
      />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" busy={upload.isPending} onClick={() => input.current?.click()}>
          <ImageUp aria-hidden className="size-4" />
          {current.logo_url ? t("logo.change") : t("logo.upload")}
        </Button>
        {current.logo_url && (
          <Button
            variant="ghost"
            className="text-danger"
            busy={remove.isPending}
            onClick={async () => {
              try {
                await remove.mutateAsync();
                toast(t("logo.removed"), "ok");
              } catch (error) {
                toast(t(`errors.${brandingErrorKey(error)}`), "error");
              }
            }}
          >
            <Trash2 aria-hidden className="size-4" />
            {t("logo.remove")}
          </Button>
        )}
      </div>
    </Card>
  );
}
