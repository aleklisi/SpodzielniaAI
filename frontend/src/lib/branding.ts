import { DEFAULT_BRANDING, sanitizeBranding, type Branding } from "@/lib/branding-css";

/**
 * The organisation's branding for the root layout (13.4), fetched server to server
 * (`API_INTERNAL_URL`, e.g. http://api:8000 in compose) and cached for 10 s. Unset or unreachable:
 * the default look, never an error page.
 */
export async function getBranding(): Promise<Branding> {
  const base = process.env.API_INTERNAL_URL;
  if (!base) return DEFAULT_BRANDING;
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/branding`, {
      next: { revalidate: 10 },
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return DEFAULT_BRANDING;
    return sanitizeBranding(await response.json());
  } catch {
    return DEFAULT_BRANDING;
  }
}
