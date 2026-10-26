"use client";

import { ImageUp, RotateCcw, Save, Trash2 } from "lucide-react";
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
  brandingError,
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

const TOKEN_LABEL: Record<string, string> = {
  primary: "Przyciski",
  "primary-contrast": "Tekst na przycisku",
  "primary-strong": "Linki i aktywne menu",
  accent: "Akcent",
  "sidebar-accent": "Menu",
};

const AGAINST_LABEL: Record<string, string> = {
  primary: "przycisk",
  bg: "tło",
  surfaces: "tła i plakietki",
  "text-muted": "tekst menu",
};

/** „Wygląd” (13.5): the organisation's name, logo and two colours, with a live preview. */
export function BrandingConsole() {
  const branding = useAdminBranding();
  if (branding.error instanceof StepUpRequired) return <StepUpNotice stepUp={branding.error.stepUp} />;
  if (branding.isPending) return <Loading />;
  if (!branding.data) {
    return (
      <Card role="alert" className="max-w-xl">
        Nie udało się wczytać ustawień wyglądu.
      </Card>
    );
  }
  // Not keyed by version: a logo change must not discard unsaved name and colour edits.
  return <Console current={branding.data} />;
}

function Console({ current }: { current: AdminBranding }) {
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
      toast("Zapisano. Członkowie zobaczą zmiany przy następnym wczytaniu strony.", "ok");
    } catch (error) {
      toast(brandingError(error), "error");
    }
  }

  const shownName = name.trim() || DEFAULT_NAME;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Wygląd"
        description="Nazwa, logo i kolory spółdzielni. Pozostałe odcienie (również w trybie ciemnym) są dobierane automatycznie tak, aby tekst był czytelny."
        actions={
          <Button variant="ghost" className="text-danger" onClick={() => setConfirmReset(true)}>
            <RotateCcw aria-hidden className="size-4" />
            Przywróć domyślne
          </Button>
        }
      />
      <div className="grid gap-6 @4xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-6">
          <Card>
            <form onSubmit={submit} className="flex flex-col gap-4 text-sm" aria-label="Nazwa i kolory">
              <h2 className="font-display text-lg font-semibold">Nazwa i kolory</h2>
              <div className="flex flex-col gap-1">
                <label htmlFor="org-name" className="font-medium">
                  Nazwa organizacji
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
                  Puste pole: „{DEFAULT_NAME}”. Widoczna w menu, na stronie głównej i w tytule karty.
                </span>
              </div>
              <label className="flex items-center gap-2">
                <input type="checkbox" className="size-4 accent-primary" checked={custom} onChange={(e) => setCustom(e.target.checked)} />
                Własne kolory
              </label>
              {custom && (
                <div className="grid gap-4 @lg:grid-cols-2">
                  <ColourField label="Kolor marki" hint="Przyciski, linki, zaznaczenia" value={brand} onChange={setBrand} />
                  <ColourField label="Kolor akcentu" hint="Plakietki i wyróżnienia" value={accent} onChange={setAccent} />
                </div>
              )}
              {preview.error && (
                <p role="alert" className="text-danger">
                  {preview.error}
                </p>
              )}
              {preview.data && preview.data.adjusted.length > 0 && (
                <p role="status" className="rounded-lg bg-surface-muted px-3 py-2">
                  {preview.data.adjusted.includes("brand") && "Kolor marki przyciemniony, aby tekst był czytelny. "}
                  {preview.data.adjusted.includes("accent") && "Kolor akcentu dostosowany, aby tekst był czytelny."}
                </p>
              )}
              <div>
                <Button type="submit" busy={save.isPending} disabled={custom && (!HEX.test(brand) || !HEX.test(accent) || !!preview.error)}>
                  <Save aria-hidden className="size-4" />
                  Zapisz
                </Button>
              </div>
            </form>
          </Card>
          <LogoCard current={current} />
        </div>
        <section aria-label="Podgląd" className="flex flex-col gap-4">
          <h2 className="font-display text-lg font-semibold">Podgląd</h2>
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
      <Dialog open={confirmReset} onClose={() => setConfirmReset(false)} title="Przywrócić domyślny wygląd?">
        <p className="text-sm">
          Nazwa, logo i kolory wrócą do „{DEFAULT_NAME}” dla wszystkich. Wgrane logo zostanie usunięte.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmReset(false)}>
            Anuluj
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
                toast("Przywrócono domyślny wygląd.", "ok");
              } catch (error) {
                toast(brandingError(error), "error");
              }
            }}
          >
            Przywróć
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

/** The server's palette for the chosen colours, asked again 300 ms after the last change. */
function useLivePreview(enabled: boolean, brand: string, accent: string): { data: Preview | null; error: string | null } {
  const { mutate } = usePreview();
  const [state, setState] = useState<{ key: string; data: Preview | null; error: string | null }>({ key: "", data: null, error: null });
  const key = `${brand.toLowerCase()}|${accent.toLowerCase()}`;
  const valid = enabled && HEX.test(brand) && HEX.test(accent);
  useEffect(() => {
    if (!valid) return;
    const timer = window.setTimeout(() => {
      mutate(
        { brand: brand.toLowerCase(), accent: accent.toLowerCase() },
        {
          onSuccess: (data) => setState({ key, data, error: null }),
          onError: (error) => setState({ key, data: null, error: brandingError(error) }),
        },
      );
    }, 300);
    return () => window.clearTimeout(timer);
  }, [valid, key, brand, accent, mutate]);
  if (!enabled) return { data: null, error: null };
  if (!HEX.test(brand) || !HEX.test(accent)) return { data: null, error: "Kolor musi mieć postać #rrggbb." };
  return state.key === key ? state : { data: null, error: null };
}

function ColourField({ label, hint, value, onChange }: { label: string; hint: string; value: string; onChange: (value: string) => void }) {
  const id = label.replace(/\s/g, "-").toLowerCase();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label}: próbnik`}
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
      <figcaption className="text-xs font-medium text-text-muted">{mode === "light" ? "Tryb jasny" : "Tryb ciemny"}</figcaption>
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
          <span className="rounded bg-sidebar-accent px-2 py-1 font-medium">Notatnik</span>
          <span className="px-2 py-1 text-text-muted">Wiki</span>
          <span className="px-2 py-1 text-text-muted">Atlas</span>
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-2 p-3 text-xs">
          <span className="font-display text-sm font-semibold">Nagłówek strony</span>
          <div className="rounded-lg border border-border bg-surface p-2">
            Karta z <span className="font-medium text-primary-strong underline">linkiem</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-lg bg-primary px-2 py-1 font-medium text-primary-contrast">Przycisk</span>
            <Badge tone="primary">Plakietka</Badge>
            <Badge tone="accent">Akcent</Badge>
          </div>
        </div>
      </div>
    </figure>
  );
}

function Ratios({ preview }: { preview: Preview }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer font-medium">Kontrast (WCAG AA)</summary>
      <ul className="mt-2 flex flex-col gap-1" aria-label="Kontrast">
        {preview.checks.map((c) => (
          <li key={`${c.mode}-${c.token}-${c.against}`} className="flex flex-wrap justify-between gap-2 border-b border-border pb-1 last:border-0">
            <span>
              {c.mode === "light" ? "Jasny" : "Ciemny"} · {TOKEN_LABEL[c.token] ?? c.token} / {AGAINST_LABEL[c.against] ?? c.against}
            </span>
            <span className={c.ratio >= c.required ? "text-ok" : "text-danger"}>
              {c.ratio.toFixed(2)}:1 (min. {c.required}:1)
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function LogoCard({ current }: { current: AdminBranding }) {
  const upload = useUploadLogo();
  const remove = useRemoveLogo();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  return (
    <Card className="flex flex-col gap-3 text-sm">
      <h2 className="font-display text-lg font-semibold">Logo</h2>
      <div className="flex min-h-20 items-center justify-center rounded-lg border border-dashed border-border bg-surface-muted p-3">
        {current.logo_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- the current same-origin logo
          <img src={current.logo_url} alt={`Logo: ${current.org_name}`} className="max-h-16 w-auto object-contain" />
        ) : (
          <span className="text-text-muted">Brak logo: menu pokazuje znak spółdzielni.</span>
        )}
      </div>
      <p className="text-xs text-text-muted">
        PNG, JPEG lub WebP do 2 MB (bez SVG). Obraz jest zapisywany ponownie, bez metadanych, w rozmiarze do 512 × 128 pikseli; z niego
        powstaje ikona karty przeglądarki.
      </p>
      <label className="sr-only" htmlFor="logo-file">
        Plik logo
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
            toast("Logo zapisane.", "ok");
          } catch (error) {
            toast(brandingError(error), "error");
          }
        }}
      />
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" busy={upload.isPending} onClick={() => input.current?.click()}>
          <ImageUp aria-hidden className="size-4" />
          {current.logo_url ? "Zmień logo" : "Wgraj logo"}
        </Button>
        {current.logo_url && (
          <Button
            variant="ghost"
            className="text-danger"
            busy={remove.isPending}
            onClick={async () => {
              try {
                await remove.mutateAsync();
                toast("Logo usunięte.", "ok");
              } catch (error) {
                toast(brandingError(error), "error");
              }
            }}
          >
            <Trash2 aria-hidden className="size-4" />
            Usuń logo
          </Button>
        )}
      </div>
    </Card>
  );
}
