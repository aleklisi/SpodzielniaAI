import { MODEL_MISSING } from "@/lib/capabilities";

/** A feature that is off until an admin configures a model for it (10.2). */
export function ModelMissing({ what }: { what: string }) {
  return (
    <p role="status" className="rounded-lg border border-warn bg-surface-muted px-3 py-2 text-sm">
      <strong>{MODEL_MISSING}.</strong> {what} jest wyłączone, dopóki administrator nie wybierze modelu w panelu „Modele AI”.
    </p>
  );
}
