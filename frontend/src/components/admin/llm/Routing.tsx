"use client";

import { useState } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import {
  eligibleModels,
  llmError,
  modelLabel,
  probeFresh,
  PURPOSE_ORDER,
  PURPOSES,
  useActivate,
  useCreateDraft,
  useDeleteDraft,
  useProbe,
  useSetRouting,
  type LlmModel,
  type LlmStatus,
  type Provider,
  type Purpose,
  type Revision,
} from "@/lib/llm-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";

export interface Change {
  purpose: Purpose;
  before: string;
  after: string;
  cloud: boolean;
}

/** What activating ``draft`` changes compared to ``active`` (for the confirmation). */
export function changes(draft: Revision, active: Revision | undefined, models: LlmModel[], providers: Provider[]): Change[] {
  const out: Change[] = [];
  for (const purpose of PURPOSE_ORDER) {
    const before = active?.routing[purpose] ?? null;
    const after = draft.routing[purpose] ?? null;
    if (before === after) continue;
    const model = models.find((m) => m.id === after);
    const provider = providers.find((p) => p.id === model?.provider_id);
    out.push({
      purpose,
      before: before ? (active?.routes?.[purpose]?.name ?? modelLabel(models.find((m) => m.id === before), providers)) : "—",
      after: after ? modelLabel(model, providers) : "—",
      cloud: provider?.kind === "CLOUD",
    });
  }
  return out;
}

export function RoutingPanel({
  status,
  providers,
  models,
  revisions,
}: {
  status: LlmStatus;
  providers: Provider[];
  models: LlmModel[];
  revisions: Revision[];
}) {
  const active = revisions.find((r) => r.status === "ACTIVE");
  const draft = revisions.filter((r) => r.status === "DRAFT").sort((a, b) => b.number - a.number)[0];
  const [confirming, setConfirming] = useState(false);
  const createDraft = useCreateDraft();
  const setRouting = useSetRouting();
  const deleteDraft = useDeleteDraft();
  const activate = useActivate();
  const probe = useProbe();
  const toast = useToast();

  async function run<T>(work: () => Promise<T>, ok?: string) {
    try {
      await work();
      if (ok) toast(ok, "ok");
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  const shown = draft ?? active;
  const pending = draft ? changes(draft, active, models, providers) : [];
  // Only models taking over a purpose need a fresh test (the backend applies the same rule).
  const unprobed = pending
    .map((c) => models.find((m) => m.id === draft?.routing[c.purpose]))
    .filter((m): m is LlmModel => m !== undefined && !probeFresh(m, status.probe_max_age_hours));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-text-muted">
          {draft
            ? `Szkic rewizji ${draft.number} (na podstawie ${draft.based_on ?? "—"}). Zmiany zaczną działać po aktywacji.`
            : `Aktywna rewizja ${active?.number ?? "—"}. Aby coś zmienić, utwórz szkic.`}
        </p>
        <div className="flex gap-2">
          {draft ? (
            <>
              <Button variant="ghost" busy={deleteDraft.isPending} onClick={() => void run(() => deleteDraft.mutateAsync(draft.number))}>
                Odrzuć szkic
              </Button>
              <Button disabled={pending.length === 0} onClick={() => setConfirming(true)}>
                Aktywuj rewizję {draft.number}
              </Button>
            </>
          ) : (
            <Button busy={createDraft.isPending} onClick={() => void run(() => createDraft.mutateAsync({}))}>
              Nowy szkic zmian
            </Button>
          )}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-text-muted">
            <tr>
              <th className="py-2 pr-3">Funkcja</th>
              <th className="py-2 pr-3">Obecnie</th>
              {draft && <th className="py-2 pr-3">W szkicu</th>}
              {draft && <th className="py-2">Test</th>}
            </tr>
          </thead>
          <tbody>
            {status.purposes
              .slice()
              .sort((a, b) => PURPOSE_ORDER.indexOf(a.purpose) - PURPOSE_ORDER.indexOf(b.purpose))
              .map((p) => {
                const current = active?.routes?.[p.purpose];
                const selectedId = shown?.routing[p.purpose] ?? "";
                const selected = models.find((m) => m.id === selectedId);
                const options = eligibleModels(p, models, providers);
                const changed = draft !== undefined && (active?.routing[p.purpose] ?? "") !== selectedId;
                return (
                  <tr key={p.purpose} className="border-t border-border align-top">
                    <td className="py-2 pr-3">
                      <div className="font-medium">{PURPOSES[p.purpose].label}</div>
                      <div className="text-xs text-text-muted">{PURPOSES[p.purpose].hint}</div>
                      {p.local_only && (
                        <Badge tone="ok" className="mt-1" title="Dane prywatne: tylko modele lokalne">
                          🔒 tylko lokalnie
                        </Badge>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {current ? (
                        <span className="flex flex-wrap items-center gap-1">
                          {current.name} <KindBadge kind={current.kind} />
                        </span>
                      ) : (
                        <span className="text-text-muted">brak (funkcja niedostępna)</span>
                      )}
                    </td>
                    {draft && (
                      <td className="py-2 pr-3">
                        <label className="sr-only" htmlFor={`route-${p.purpose}`}>
                          Model dla: {PURPOSES[p.purpose].label}
                        </label>
                        <select
                          id={`route-${p.purpose}`}
                          className={`${INPUT} ${changed ? "border-primary" : ""}`}
                          value={selectedId}
                          disabled={p.purpose === "EMBED" || setRouting.isPending}
                          onChange={(e) =>
                            void run(() =>
                              setRouting.mutateAsync({ number: draft.number, routes: { [p.purpose]: e.target.value || null } }),
                            )
                          }
                        >
                          <option value="">— brak —</option>
                          {selected && !options.includes(selected) && <option value={selected.id}>{modelLabel(selected, providers)}</option>}
                          {options.map((m) => (
                            <option key={m.id} value={m.id}>
                              {modelLabel(m, providers)}
                              {providers.find((pr) => pr.id === m.provider_id)?.kind === "CLOUD" ? " (chmura)" : ""}
                            </option>
                          ))}
                        </select>
                        {p.purpose === "EMBED" && (
                          <p className="mt-1 text-xs text-text-muted">Zmiana wymaga przebudowy indeksu (poza panelem).</p>
                        )}
                      </td>
                    )}
                    {draft && (
                      <td className="py-2">
                        {selected &&
                          changed &&
                          (probeFresh(selected, status.probe_max_age_hours) ? (
                            <span className="text-ok">✓ przetestowany</span>
                          ) : (
                            <Button
                              variant="secondary"
                              busy={probe.isPending && probe.variables === selected.id}
                              onClick={() => void run(() => probe.mutateAsync(selected.id), "Test zakończony.")}
                            >
                              Testuj
                            </Button>
                          ))}
                      </td>
                    )}
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {draft && (
        <ActivateDialog
          open={confirming}
          number={draft.number}
          changes={pending}
          unprobed={unprobed.map((m) => modelLabel(m, providers))}
          busy={activate.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() =>
            void run(async () => {
              await activate.mutateAsync(draft.number);
              setConfirming(false);
            }, `Rewizja ${draft.number} jest aktywna. Serwery przełączą się przy następnym wywołaniu.`)
          }
        />
      )}
    </div>
  );
}

function ActivateDialog({
  open,
  number,
  changes: list,
  unprobed,
  busy,
  onClose,
  onConfirm,
}: {
  open: boolean;
  number: number;
  changes: Change[];
  unprobed: string[];
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const cloud = list.filter((c) => c.cloud);
  return (
    <Dialog open={open} onClose={onClose} title={`Aktywować rewizję ${number}?`}>
      <div className="flex flex-col gap-3 text-sm">
        <ul className="flex flex-col gap-1" aria-label="Zmiany">
          {list.map((c) => (
            <li key={c.purpose}>
              <strong>{PURPOSES[c.purpose].label}:</strong> {c.before} → {c.after}
            </li>
          ))}
        </ul>
        {cloud.length > 0 && (
          <Card role="alert" className="border-warn p-3">
            Do modelu chmurowego trafią zapytania funkcji: {cloud.map((c) => PURPOSES[c.purpose].label).join(", ")}. Treści
            kanonicznej Wiki zostaną przesłane do zewnętrznego dostawcy.
          </Card>
        )}
        {unprobed.length > 0 && (
          <p role="status" className="text-warn">
            Bez aktualnego testu: {unprobed.join(", ")}. Serwer odrzuci aktywację, dopóki ich nie przetestujesz.
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button busy={busy} onClick={onConfirm}>
            Aktywuj
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
