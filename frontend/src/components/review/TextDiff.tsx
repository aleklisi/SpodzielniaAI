import { diffWords } from "diff";

/** Word-level difference between the article being superseded and its replacement. */
export function TextDiff({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after);
  return (
    <p className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-sm" aria-label="Zmiany względem zastępowanego artykułu">
      {parts.map((part, i) =>
        part.added ? (
          <ins key={i} className="bg-ok/20 no-underline">
            {part.value}
          </ins>
        ) : part.removed ? (
          <del key={i} className="bg-danger/20">
            {part.value}
          </del>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </p>
  );
}
