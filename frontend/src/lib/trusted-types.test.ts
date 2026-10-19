import { describe, expect, it, vi } from "vitest";

import { allowedScriptUrl, installDefaultPolicy } from "@/lib/trusted-types";

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

  it("installs once, with script URLs only", () => {
    const createPolicy = vi.fn();
    const win = { trustedTypes: { createPolicy }, location: site } as unknown as Window;
    expect(installDefaultPolicy(win)).toBe(true);
    const [name, rules] = createPolicy.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe("default");
    expect(Object.keys(rules)).toEqual(["createScriptURL"]);
    expect(installDefaultPolicy({ location: site } as unknown as Window)).toBe(false);
  });
});
