import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SafeMarkdown, safeHref } from "@/components/markdown/SafeMarkdown";

const HOSTILE = [
  '<img src=x onerror="alert(1)">',
  "<script>alert(1)</script>",
  "<svg><script>alert(1)</script></svg>",
  '<iframe src="https://evil.example"></iframe>',
  "[klik](javascript:alert(1))",
  "[dane](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "[http](http://evil.example)",
  "![obraz](https://evil.example/pixel.png)",
  '<a href="javascript:alert(1)">x</a>',
  "<style>body{display:none}</style>",
  "[x](JaVaScRiPt:alert(1))",
  "[x](//evil.example/path)",
];

describe("SafeMarkdown", () => {
  it.each(HOSTILE)("renders %s inert", (payload) => {
    const { container } = render(<SafeMarkdown markdown={`Tekst ${payload} koniec`} />);
    expect(container.querySelector("script, img, iframe, svg, style, object, embed")).toBeNull();
    for (const el of container.querySelectorAll("*")) {
      for (const attr of el.getAttributeNames()) expect(attr.startsWith("on")).toBe(false);
    }
    for (const a of container.querySelectorAll("a")) {
      const href = a.getAttribute("href") ?? "";
      expect(href.startsWith("https://") || href.startsWith("#") || href.startsWith("/")).toBe(true);
      expect(href.startsWith("//")).toBe(false);
    }
  });

  it("keeps ordinary formatting and https links", () => {
    const { container } = render(
      <SafeMarkdown markdown={"**Warroza** to *roztocz*.\n\n- jeden\n- dwa\n\n[PIW](https://www.wetgiw.gov.pl)"} />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("Warroza");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://www.wetgiw.gov.pl");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer nofollow");
  });

  it("turns citation markers into links to the cards", () => {
    const { container } = render(<SafeMarkdown markdown="Zimą leczymy [1]. Osyp liczymy [2]." citations />);
    expect([...container.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["[1]", "#cite-1"],
      ["[2]", "#cite-2"],
    ]);
  });

  it("accepts only https and same-origin targets", () => {
    expect(safeHref("https://a.example")).toBe("https://a.example");
    expect(safeHref("/wiki/1")).toBe("/wiki/1");
    expect(safeHref("#cite-1")).toBe("#cite-1");
    for (const bad of ["http://a.example", "javascript:x", "data:x", "//a.example", "mailto:x@y", undefined]) {
      expect(safeHref(bad)).toBeUndefined();
    }
  });
});
