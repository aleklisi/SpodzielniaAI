/**
 * The collapsed/expanded menu preference (12.1). A cookie, not localStorage, so the root layout
 * renders the right width on the first paint. It holds "1" or "0" and nothing else.
 */
export const SIDEBAR_COOKIE = "sb";

export function isCollapsed(value: string | undefined): boolean {
  return value === "1";
}

export function saveCollapsed(collapsed: boolean): void {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${SIDEBAR_COOKIE}=${collapsed ? "1" : "0"}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
}
