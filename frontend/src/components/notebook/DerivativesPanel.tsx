"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DERIVATIVE_KIND, DERIVATIVE_STATUS } from "@/lib/messages";
import { flaggedSpans, segment, useConfirmQuarantine, type Derivative } from "@/lib/notebook";

const TONE = { DONE: "ok", FAILED: "danger", QUARANTINED: "warn", PENDING: "neutral", PROCESSING: "neutral" } as const;

export function DerivativesPanel({ noteId, derivatives }: { noteId: string; derivatives: Derivative[] }) {
  if (derivatives.length === 0) return null;
  return (
    <section aria-labelledby="derivatives-title" className="flex flex-col gap-3">
      <h2 id="derivatives-title" className="font-display text-lg font-semibold">
        Przetworzone materiały
      </h2>
      {derivatives.map((d) =>
        d.status === "QUARANTINED" && !d.quarantine_confirmed_at ? (
          <QuarantineView key={d.id} noteId={noteId} derivative={d} />
        ) : (
          <Card key={d.id} className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{DERIVATIVE_KIND[d.kind] ?? d.kind}</span>
              <Badge tone={TONE[d.status]}>{DERIVATIVE_STATUS[d.status] ?? d.status}</Badge>
            </div>
            {d.source && <p className="truncate text-xs text-text-muted">{d.source}</p>}
            {d.content && <p className="max-h-48 overflow-y-auto whitespace-pre-wrap text-sm">{d.content}</p>}
            {d.status === "FAILED" && <p className="text-sm text-danger">Nie udało się przetworzyć materiału.</p>}
          </Card>
        ),
      )}
    </section>
  );
}

/**
 * Text the injection scanner (3.3) quarantined: shown with the flagged spans highlighted. It
 * stays out of promotions and the notebook index until the owner confirms it is harmless.
 * The text is rendered as React text nodes: nothing in it is interpreted.
 */
export function QuarantineView({ noteId, derivative }: { noteId: string; derivative: Derivative }) {
  const confirm = useConfirmQuarantine(noteId);
  const pieces = segment(derivative.content ?? "", flaggedSpans(derivative));
  return (
    <Card className="flex flex-col gap-3 border-warn" role="region" aria-label="Materiał w kwarantannie">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{DERIVATIVE_KIND[derivative.kind] ?? derivative.kind}</span>
        <Badge tone="warn">Kwarantanna</Badge>
      </div>
      <p className="text-sm">
        Ten materiał zawiera fragmenty wyglądające jak polecenia dla modelu AI (np. ukryty tekst w PDF). Zaznaczyliśmy je
        poniżej. Dopóki nie potwierdzisz, że treść jest bezpieczna, nie trafi do wniosków do Wiki.
      </p>
      <p className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-muted p-3 text-sm">
        {pieces.map((piece, i) =>
          piece.flagged ? (
            <mark key={i} className="rounded bg-warn/30 px-0.5 text-text" title="Podejrzany fragment">
              {piece.text}
            </mark>
          ) : (
            <span key={i}>{piece.text}</span>
          ),
        )}
      </p>
      <div>
        <Button variant="secondary" busy={confirm.isPending} onClick={() => confirm.mutate(derivative.id)}>
          Potwierdzam, że treść jest bezpieczna
        </Button>
        {confirm.isError && <p role="alert" className="mt-2 text-sm text-danger">Nie udało się potwierdzić. Spróbuj ponownie.</p>}
      </div>
    </Card>
  );
}
