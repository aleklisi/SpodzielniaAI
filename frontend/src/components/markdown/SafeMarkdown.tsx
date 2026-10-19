import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize, { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

/**
 * Markdown from the LLM and from Wiki blocks, rendered inert (6.3, 6.5):
 *
 * - no raw HTML: `rehype-raw` is not used, so `<script>`, `<img onerror>`, `<iframe>` … in the
 *   Markdown stay text;
 * - `rehype-sanitize` with an allow-list of structural tags and no attributes beyond `href`;
 * - links: only `https:` and same-origin paths/fragments; everything else (`javascript:`,
 *   `data:`, `http:` …) loses its href. External links get rel="noopener noreferrer nofollow";
 * - images are never rendered (LLM output must not make the browser fetch anything).
 *
 * With `citations`, `[n]` markers become links to `#cite-n` (the citation cards, 6.5).
 */

const SCHEMA: SanitizeSchema = {
  ...defaultSchema,
  tagNames: [
    "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "h2", "h3", "h4", "a", "sup",
    "table", "thead", "tbody", "tr", "th", "td",
  ],
  attributes: { a: ["href"], th: ["align"], td: ["align"] },
  protocols: { href: ["https"] },
  clobber: [],
  strip: ["script", "style"],
};

export function safeHref(href: string | undefined): string | undefined {
  if (!href) return undefined;
  if (href.startsWith("#") || (href.startsWith("/") && !href.startsWith("//"))) return href;
  try {
    return new URL(href).protocol === "https:" ? href : undefined;
  } catch {
    return undefined;
  }
}

const COMPONENTS: Components = {
  a: ({ href, children }) => {
    const safe = safeHref(href);
    if (!safe) return <span>{children}</span>;
    const external = safe.startsWith("https:");
    return (
      <a
        href={safe}
        className="text-primary-strong underline"
        {...(external ? { rel: "noopener noreferrer nofollow", target: "_blank" } : {})}
      >
        {children}
      </a>
    );
  },
  img: () => null,
  h1: ({ children }) => <h2 className="mt-4 font-display text-lg font-semibold">{children}</h2>,
  h2: ({ children }) => <h3 className="mt-4 font-display font-semibold">{children}</h3>,
  ul: ({ children }) => <ul className="list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5">{children}</ol>,
  code: ({ children }) => <code className="rounded bg-surface-muted px-1 text-sm">{children}</code>,
  blockquote: ({ children }) => <blockquote className="border-l-4 border-border pl-3 text-text-muted">{children}</blockquote>,
};

const MARKER = /\[(\d{1,2})\](?!\()/g;

export function SafeMarkdown({ markdown, citations = false }: { markdown: string; citations?: boolean }) {
  const source = citations ? markdown.replace(MARKER, (_, n: string) => `[[${n}]](#cite-${n})`) : markdown;
  return (
    <div className="flex flex-col gap-2 leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, SCHEMA]]}
        components={COMPONENTS}
        skipHtml
        urlTransform={(url) => safeHref(url) ?? ""}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
