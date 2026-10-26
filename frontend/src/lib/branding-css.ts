import type { components } from "@/lib/api-schema";

export type Branding = components["schemas"]["BrandingOut"];

export const DEFAULT_NAME = "Spółdzielnia AI";

export const DEFAULT_BRANDING: Branding = {
  org_name: DEFAULT_NAME,
  custom_name: false,
  version: 0,
  palette: null,
  logo_url: null,
  favicon_urls: null,
};

/** The tokens branding may set (globals.css keeps every other colour, 13.4). */
export const TOKENS = ["primary", "primary-strong", "primary-contrast", "accent", "sidebar", "sidebar-accent"] as const;

const HEX = /^#[0-9a-f]{6}$/;
// Same-origin files of the current logo only (`/api/branding/files/<version>/<name>.png`).
const LOGO_URL = /^\/api\/branding\/files\/\d+\/(logo|favicon-32|favicon-180)\.png$/;

function declarations(tokens: Record<string, string> | undefined): string | null {
  if (!tokens) return null;
  const out: string[] = [];
  for (const token of TOKENS) {
    const value = tokens[token];
    if (typeof value !== "string" || !HEX.test(value)) return null;
    out.push(`--${token}:${value}`);
  }
  return out.join(";");
}

/**
 * The CSS for the branded tokens, or null for the default look. The API, the database (0027) and
 * this check all allow only `#rrggbb` values under known tokens: one bad value, and the whole
 * palette is ignored (rule R2). `:root:root` wins over globals.css whatever the stylesheet order.
 */
export function brandingCss(palette: Branding["palette"]): string | null {
  if (!palette) return null;
  const light = declarations(palette.light);
  const dark = declarations(palette.dark);
  if (!light || !dark) return null;
  return `:root:root{${light}}@media (prefers-color-scheme: dark){:root:root{${dark}}}`;
}

/** The branding as the page may use it: unknown or off-origin logo URLs are dropped. */
export function sanitizeBranding(raw: unknown): Branding {
  if (!raw || typeof raw !== "object") return DEFAULT_BRANDING;
  const b = raw as Partial<Branding>;
  const name = typeof b.org_name === "string" && b.org_name.trim() ? b.org_name.slice(0, 60) : DEFAULT_NAME;
  const url = (value: unknown) => (typeof value === "string" && LOGO_URL.test(value) ? value : null);
  const logo = url(b.logo_url);
  const favicon32 = url(b.favicon_urls?.["32"]);
  const favicon180 = url(b.favicon_urls?.["180"]);
  return {
    org_name: name,
    custom_name: b.custom_name === true,
    version: typeof b.version === "number" ? b.version : 0,
    palette: brandingCss(b.palette ?? null) ? (b.palette ?? null) : null,
    logo_url: logo,
    favicon_urls: logo && favicon32 && favicon180 ? { "32": favicon32, "180": favicon180 } : null,
  };
}
