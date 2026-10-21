"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  atlasError,
  LABEL_STATUS,
  useCreateLabel,
  useCuratedLabels,
  useDomains,
  useEditLabel,
  useLabelAction,
  useMergeLabel,
  type CuratedLabel,
  type LabelStatus,
} from "@/lib/atlas";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2 text-sm";

function splitSynonyms(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** „Słownik etykiet”: the experts' view of one domain's labels, proposals first. */
export function LabelVocabulary() {
  const domains = useDomains();
  const [chosen, setChosen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<LabelStatus | "">("PROPOSED");
  const domainId = chosen ?? domains.data?.find((d) => d.status === "ACTIVE")?.id ?? null;
  const labels = useCuratedLabels(domainId, q.trim(), status);
  const [editing, setEditing] = useState<CuratedLabel | "new" | null>(null);
  const [merging, setMerging] = useState<CuratedLabel | null>(null);
  const action = useLabelAction();
  const toast = useToast();

  if (labels.error instanceof StepUpRequired) return <StepUpNotice stepUp={labels.error.stepUp} />;

  async function run(label: CuratedLabel, kind: "approve" | "reject" | "deprecate") {
    try {
      await action.mutateAsync({ id: label.id, action: kind });
      toast({ approve: "Etykieta zatwierdzona.", reject: "Propozycja odrzucona.", deprecate: "Etykieta wycofana." }[kind], "ok");
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else toast(atlasError(e), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">Słownik etykiet</h1>
          <p className="text-sm text-text-muted">
            Etykiety oznaczeń na zdjęciach atlasu, osobno dla każdej dziedziny. <Link href="/review" className="underline">Wróć do recenzji</Link>
          </p>
        </div>
        <Button onClick={() => setEditing("new")} disabled={!domainId}>
          Nowa etykieta
        </Button>
      </div>
      {domains.data?.length === 0 && (
        <Card className="text-text-muted">Nie ma jeszcze dziedzin. Administrator tworzy je w panelu „Dziedziny atlasu”.</Card>
      )}
      <div className="flex flex-wrap gap-3 text-sm">
        <label className="flex flex-col gap-1">
          Dziedzina
          <select className={INPUT} value={domainId ?? ""} onChange={(e) => setChosen(e.target.value)}>
            {domains.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {d.status === "ARCHIVED" ? " (archiwum)" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Status
          <select className={INPUT} value={status} onChange={(e) => setStatus(e.target.value as LabelStatus | "")}>
            <option value="">Wszystkie</option>
            {(Object.keys(LABEL_STATUS) as LabelStatus[]).map((s) => (
              <option key={s} value={s}>
                {LABEL_STATUS[s].label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Szukaj
          <input className={INPUT} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="nazwa lub synonim" />
        </label>
      </div>
      {labels.isError && !(labels.error instanceof StepUpRequired) && (
        <p role="alert" className="text-danger">
          Nie udało się wczytać etykiet.
        </p>
      )}
      {labels.data?.length === 0 && <p className="text-text-muted">Brak etykiet dla tych filtrów.</p>}
      <ul className="flex flex-col gap-2" aria-label="Etykiety">
        {labels.data?.map((label) => (
          <li key={label.id}>
            <Card className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">
                  {label.name} <Badge tone={LABEL_STATUS[label.status].tone}>{LABEL_STATUS[label.status].label}</Badge>
                </p>
                {label.description && <p className="text-sm text-text-muted">{label.description}</p>}
                {label.synonyms.length > 0 && <p className="text-xs text-text-muted">Synonimy: {label.synonyms.join(", ")}</p>}
                {label.status === "PROPOSED" && (
                  <p className="text-xs text-text-muted">Zaproponował(a): {label.proposed_by_name ?? "członek spółdzielni"}</p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {label.status === "PROPOSED" && (
                  <>
                    <Button onClick={() => void run(label, "approve")}>Zatwierdź</Button>
                    <Button variant="secondary" onClick={() => setMerging(label)}>
                      Scal z…
                    </Button>
                    <Button variant="ghost" onClick={() => void run(label, "reject")}>
                      Odrzuć
                    </Button>
                  </>
                )}
                {label.status === "ACTIVE" && (
                  <>
                    <Button variant="secondary" onClick={() => setEditing(label)}>
                      Edytuj
                    </Button>
                    <Button variant="secondary" onClick={() => setMerging(label)}>
                      Scal z…
                    </Button>
                    <Button variant="ghost" onClick={() => void run(label, "deprecate")}>
                      Wycofaj
                    </Button>
                  </>
                )}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {domainId && editing && (
        <LabelDialog key={editing === "new" ? "new" : editing.id} domainId={domainId} label={editing === "new" ? null : editing} onClose={() => setEditing(null)} />
      )}
      {domainId && merging && <MergeDialog key={merging.id} domainId={domainId} label={merging} onClose={() => setMerging(null)} />}
    </div>
  );
}

function LabelDialog({ domainId, label, onClose }: { domainId: string; label: CuratedLabel | null; onClose: () => void }) {
  const [name, setName] = useState(label?.name ?? "");
  const [description, setDescription] = useState(label?.description ?? "");
  const [synonyms, setSynonyms] = useState(label?.synonyms.join(", ") ?? "");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateLabel();
  const edit = useEditLabel();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const body = { name: name.trim(), description: description.trim(), synonyms: splitSynonyms(synonyms) };
    try {
      if (label) await edit.mutateAsync({ id: label.id, ...body });
      else await create.mutateAsync({ domain_id: domainId, ...body });
      onClose();
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else setError(atlasError(e));
    }
  }

  return (
    <Dialog open onClose={onClose} title={label ? "Edytuj etykietę" : "Nowa etykieta"}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Etykieta">
        <label className="flex flex-col gap-1">
          Nazwa
          <input className={INPUT} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          Opis (jak rozpoznać)
          <textarea className={INPUT} maxLength={1000} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          Synonimy (po przecinku)
          <input className={INPUT} value={synonyms} onChange={(e) => setSynonyms(e.target.value)} />
        </label>
        {error && (
          <p role="alert" className="text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button type="submit" busy={create.isPending || edit.isPending}>
            Zapisz
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function MergeDialog({ domainId, label, onClose }: { domainId: string; label: CuratedLabel; onClose: () => void }) {
  const active = useCuratedLabels(domainId, "", "ACTIVE");
  const [into, setInto] = useState("");
  const [error, setError] = useState<string | null>(null);
  const merge = useMergeLabel();
  const targets = (active.data ?? []).filter((l) => l.id !== label.id);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await merge.mutateAsync({ id: label.id, into });
      onClose();
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else setError(atlasError(e));
    }
  }

  return (
    <Dialog open onClose={onClose} title={`Scal „${label.name}”`}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Scalanie">
        <p className="text-text-muted">
          „{label.name}” zostanie wycofana i stanie się synonimem wybranej etykiety. Oznaczenia z nią pokażą etykietę docelową.
        </p>
        <label className="flex flex-col gap-1">
          Etykieta docelowa
          <select className={INPUT} required value={into} onChange={(e) => setInto(e.target.value)}>
            <option value="">Wybierz…</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        {error && (
          <p role="alert" className="text-danger">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button type="submit" busy={merge.isPending} disabled={!into}>
            Scal
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
