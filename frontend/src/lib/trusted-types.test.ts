import { describe, expect, it, vi } from "vitest";

import { allowedScriptUrl, installDefaultPolicy, sanitizeHtml } from "@/lib/trusted-types";

const site = { href: "https://spoldzielnia.localhost/wiki", origin: "https://spoldzielnia.localhost" };

describe("Trusted Types default policy", () => {
  it("allows only our own Next.js chunks", () => {
    expect(allowedScriptUrl("/_next/static/chunks/a.js", site)).toBe("/_next/static/chunks/a.js");
    expect(allowedScriptUrl("https://evil.example/_next/static/a.js", site)).toBeNull();
    expect(allowedScriptUrl("/api/rag/ask", site)).toBeNull();
    expect(allowedScriptUrl("javascript:alert(1)", site)).toBeNull();
    expect(allowedScriptUrl("data:text/javascript,alert(1)", site)).toBeNull();
    expect(allowedScriptUrl("/_next/static/../../evil.js", site)).toBeNull();
  });

  it("installs once: script URLs and sanitised HTML, never script strings", () => {
    const createPolicy = vi.fn();
    const win = { trustedTypes: { createPolicy }, location: site } as unknown as Window;
    expect(installDefaultPolicy(win)).toBe(true);
    const [name, rules] = createPolicy.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe("default");
    expect(Object.keys(rules).sort()).toEqual(["createHTML", "createScriptURL"]);
    expect(installDefaultPolicy({ location: site } as unknown as Window)).toBe(false);
  });

  it.each([
    ['<img src=x onerror="alert(1)">', "onerror"],
    ["<script>alert(1)</script><p>tekst</p>", "<script"],
    ['<a href="javascript:alert(1)">x</a>', "javascript:"],
    ['<svg><script>alert(1)</script></svg>', "<script"],
    ['<iframe src="https://evil.example"></iframe>', "<iframe"],
    ['<p style="background:url(x)">y</p>', "style="],
  ])("sanitises HTML sinks: %s", (html, forbidden) => {
    expect(sanitizeHtml(html).toLowerCase()).not.toContain(forbidden);
  });

  it("keeps ordinary formatting", () => {
    expect(sanitizeHtml("<p><strong>Warroza</strong> <em>zimą</em></p>")).toBe(
      "<p><strong>Warroza</strong> <em>zimą</em></p>",
    );
  });
});
