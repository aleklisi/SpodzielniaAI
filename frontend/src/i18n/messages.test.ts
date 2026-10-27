/**
 * The catalogues (14.2): English has exactly Polish's keys, every message is valid ICU with the
 * same arguments in both languages, and English is a translation, not a copy.
 */
import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import { MESSAGES } from "@/messages";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

/** Argument names and rich-text tags, sorted: what a caller has to pass. */
function argumentsOf(elements: MessageFormatElement[], found = new Set<string>()): Set<string> {
  for (const element of elements) {
    if (element.type === TYPE.literal || element.type === TYPE.pound) continue;
    found.add(element.type === TYPE.tag ? `<${element.value}>` : element.value);
    if (element.type === TYPE.tag) argumentsOf(element.children, found);
    if (element.type === TYPE.plural || element.type === TYPE.select) {
      for (const option of Object.values(element.options)) argumentsOf(option.value, found);
    }
  }
  return found;
}

const pl = flatten(MESSAGES.pl as unknown as Tree);
const en = flatten(MESSAGES.en as unknown as Tree);

// The same in both languages on purpose: names, codes and symbols.
const SAME = new Set([
  "titles.atlas",
  "titles.adminAtlas",
  "shell.nav.wiki",
  "shell.nav.atlas",
  "shell.nav.adminAtlas",
  "shell.menu",
  "atlas.gallery.title",
  "atlas.photo.backShort",
  "review.atlas",
  "privacy.region",
  "adminAtlas.export.format",
  "adminBranding.logo.title",
  "adminBranding.sample.accent",
  "adminBranding.minimum",
  "adminLlm.tabs.playground",
  "adminLlm.models.model",
  "adminLlm.models.generation",
  "adminLlm.routing.test",
  "adminLlm.checks.embed",
  "adminLlm.checks.json_json_schema",
  "adminLlm.panel.mfa",
  "adminLlm.index.counts",
  "adminSecurity.periods.1",
  "adminSecurity.table.d1",
  "adminUsers.panel.email",
  "adminUsers.create.email",
  "adminUsers.panel.mfa",
  "adminUsers.range",
  "atlas.gallery.range",
  "adminUsers.roleNames.ADMIN",
  "adminUsers.status",
  "contributions.status",
  "wiki.drafts.status",
  "atlas.vocabulary.status",
  "adminLlm.clearance.internal",
  "adminBranding.tokens.sidebar-accent",
  "adminBranding.logo.alt",
]);

describe("message catalogues", () => {
  it("English has exactly the Polish keys", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(pl).sort());
  });

  it.each(Object.keys(pl))("%s parses with the same arguments in both languages", (key) => {
    const polish = argumentsOf(parse(pl[key]));
    const english = argumentsOf(parse(en[key]));
    expect([...english].sort()).toEqual([...polish].sort());
  });

  it("English is translated, not copied", () => {
    const copied = Object.keys(pl).filter((key) => pl[key] === en[key] && pl[key] !== "" && !SAME.has(key));
    expect(copied).toEqual([]);
  });

  it("no English message carries Polish letters", () => {
    expect(Object.entries(en).filter(([, text]) => /[ąćęłńóśźż]/i.test(text)).map(([key]) => key)).toEqual([]);
  });
});
