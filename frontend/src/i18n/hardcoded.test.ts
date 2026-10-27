/**
 * No user-visible text outside the message catalogues (14.4, rule R5). ESLint's
 * `i18next/no-literal-string` catches JSX text; this catches the rest:
 *
 * - Polish letters anywhere in code (string literals, templates, JSX), comments excluded;
 * - literal text in the attributes a person reads or hears (`aria-label`, `title`, …).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");

// Reasoned exceptions: path → allowed snippets.
const ALLOWED_POLISH: Record<string, string[]> = {
  "lib/branding-css.ts": ["Spółdzielnia AI"], // the default organisation name: a brand, never translated
};
const ALLOWED_ATTRIBUTES = new Set(["UUID", "https://", "http://llm:11434/v1"]);
const ATTRIBUTES = ["aria-label", "title", "placeholder", "alt", "label", "description", "reason", "what", "caption"];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "messages" || name === "test" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".d.ts") ? [path] : [];
  });
}

/** The code without comments (block comments, and `//` comments that start a line or follow a space). */
function code(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/.*$/gm, "$1");
}

const FILES = sources(ROOT).map((path) => ({ path: relative(ROOT, path), text: code(readFileSync(path, "utf8")) }));

describe("no hard-coded text", () => {
  it("finds the sources", () => {
    expect(FILES.length).toBeGreaterThan(100);
  });

  it("has no Polish letters outside the catalogues", () => {
    const found = FILES.flatMap(({ path, text }) => {
      let rest = text;
      for (const allowed of ALLOWED_POLISH[path] ?? []) rest = rest.split(allowed).join("");
      return rest
        .split("\n")
        .filter((line) => /[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ„”]/.test(line))
        .map((line) => `${path}: ${line.trim()}`);
    });
    expect(found).toEqual([]);
  });

  it("has no literal text in labels, titles and placeholders", () => {
    const pattern = new RegExp(`\\b(?:${ATTRIBUTES.join("|")})="([^"]*)"`, "g");
    const found = FILES.flatMap(({ path, text }) =>
      [...text.matchAll(pattern)]
        .filter(([, value]) => /\p{L}{2}/u.test(value) && !ALLOWED_ATTRIBUTES.has(value))
        .map(([match]) => `${path}: ${match}`),
    );
    expect(found).toEqual([]);
  });
});
