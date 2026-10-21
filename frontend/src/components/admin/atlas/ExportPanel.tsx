"use client";

import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StepUpRequired } from "@/lib/api";
import { useDomains, useLabels } from "@/lib/atlas";
import { downloadUrl, EXPORT_STATUS, exportError, useCreateExport, useExports } from "@/lib/atlas-export";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2 text-sm";
const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "short" });

/**
 * „Eksport zbioru danych”: approved photos whose authors allowed export, as COCO or YOLO. The
 * download link works once and is known only to this page (the token is shown once).
 */
export function ExportPanel() {
  const exports = useExports();
  const domains = useDomains();
  const create = useCreateExport();
  const [domainId, setDomainId] = useState("");
  const [format, setFormat] = useState<"COCO" | "YOLO">("COCO");
  const [labelIds, setLabelIds] = useState<string[]>([]);
  const [since, setSince] = useState("");
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const labels = useLabels(domainId || null, "");

  if (exports.data && !exports.data.enabled) {
    return (
      <section aria-labelledby="export-title" className="flex flex-col gap-2">
        <h2 id="export-title" className="font-display text-xl font-semibold">
          Eksport zbioru danych
        </h2>
        <Card className="text-sm text-text-muted">
          Eksport jest wyłączony. Operator serwera może go włączyć ustawieniem <code>ATLAS_EXPORT_ENABLED=true</code> w pliku <code>.env</code>.
        </Card>
      </section>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await create.mutateAsync({
        format,
        domain_id: domainId,
        label_ids: labelIds.length ? labelIds : null,
        min_verified_at: since ? new Date(`${since}T00:00:00Z`).toISOString() : null,
      });
      setTokens((t) => ({ ...t, [created.export.id]: created.download_token }));
    } catch (e) {
      if (e instanceof StepUpRequired) setStepUp(e.stepUp);
      else setError(exportError(e));
    }
  }

  return (
    <section aria-labelledby="export-title" className="flex flex-col gap-4">
      <div>
        <h2 id="export-title" className="font-display text-xl font-semibold">
          Eksport zbioru danych
        </h2>
        <p className="text-sm text-text-muted">
          Zatwierdzone zdjęcia jednej dziedziny, których autorzy zgodzili się na eksport, z oznaczeniami w formacie COCO lub YOLO. Pobranego
          pliku nie da się wycofać: później wycofane zdjęcia są tylko oznaczane na liście poniżej.
        </p>
      </div>
      {stepUp ? (
        <StepUpNotice stepUp={stepUp} reason="Eksport zbioru danych wymaga świeżego logowania." />
      ) : (
        <Card>
          <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Nowy eksport">
            <div className="flex flex-wrap gap-3">
              <label className="flex flex-col gap-1">
                Dziedzina
                <select
                  className={INPUT}
                  required
                  value={domainId}
                  onChange={(e) => {
                    setDomainId(e.target.value);
                    setLabelIds([]);
                  }}
                >
                  <option value="">Wybierz…</option>
                  {domains.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="flex flex-col gap-1">
                <legend>Format</legend>
                <span className="flex gap-3">
                  {(["COCO", "YOLO"] as const).map((f) => (
                    <label key={f} className="flex items-center gap-1">
                      <input type="radio" name="export-format" checked={format === f} onChange={() => setFormat(f)} />
                      {f}
                    </label>
                  ))}
                </span>
              </fieldset>
              <label className="flex flex-col gap-1">
                Zweryfikowane od (opcjonalnie)
                <input className={INPUT} type="date" value={since} onChange={(e) => setSince(e.target.value)} />
              </label>
            </div>
            {domainId && (labels.data?.length ?? 0) > 0 && (
              <fieldset className="flex flex-col gap-1">
                <legend>Tylko etykiety (puste: wszystkie)</legend>
                <span className="flex flex-wrap gap-3">
                  {labels.data
                    ?.filter((l) => l.status === "ACTIVE")
                    .map((l) => (
                      <label key={l.id} className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={labelIds.includes(l.id)}
                          onChange={(e) => setLabelIds((ids) => (e.target.checked ? [...ids, l.id] : ids.filter((i) => i !== l.id)))}
                        />
                        {l.name}
                      </label>
                    ))}
                </span>
              </fieldset>
            )}
            {error && (
              <p role="alert" className="text-danger">
                {error}
              </p>
            )}
            <Button type="submit" busy={create.isPending} disabled={!domainId} className="self-start">
              Utwórz eksport
            </Button>
          </form>
        </Card>
      )}
      {exports.data && exports.data.exports.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Eksporty</caption>
          <thead className="text-text-muted">
            <tr>
              <th className="py-1">Utworzony</th>
              <th>Dziedzina</th>
              <th>Format</th>
              <th>Zdjęcia</th>
              <th>Stan</th>
              <th>
                <span className="sr-only">Pobierz</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {exports.data.exports.map((e) => (
              <tr key={e.id} className="border-t border-border">
                <td className="py-2">
                  {DATE.format(new Date(e.created_at))}
                  <span className="block text-xs text-text-muted">{e.created_by_name}</span>
                </td>
                <td>{e.domain_name}</td>
                <td>{e.format}</td>
                <td>
                  {e.image_count} ({e.region_count} oznaczeń)
                  {e.revoked_since > 0 && <span className="block text-xs text-warn">wycofane od eksportu: {e.revoked_since}</span>}
                </td>
                <td>
                  <Badge tone={e.status === "READY" ? "ok" : e.status === "FAILED" ? "danger" : "neutral"}>{EXPORT_STATUS[e.status]}</Badge>
                </td>
                <td>
                  {e.status === "READY" && tokens[e.id] ? (
                    <a
                      className="font-medium text-primary-strong underline"
                      href={downloadUrl(e.id, tokens[e.id])}
                      onClick={() => setTokens((t) => Object.fromEntries(Object.entries(t).filter(([id]) => id !== e.id)))}
                    >
                      Pobierz (jednorazowo)
                    </a>
                  ) : e.status === "READY" ? (
                    <span className="text-xs text-text-muted">link pokazany przy tworzeniu</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
