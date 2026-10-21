"use client";

import { useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { atlasError, useCreateDomain, useDomains, useUpdateDomain, type Domain } from "@/lib/atlas";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2 text-sm";

/** „Dziedziny atlasu”: admins create, rename and archive domains; experts fill them with labels. */
export function DomainsPanel() {
  const domains = useDomains();
  const create = useCreateDomain();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await create.mutateAsync({ name: name.trim(), description: description.trim() || null });
      setName("");
      setDescription("");
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else setError(atlasError(e));
    }
  }

  return (
    <section aria-labelledby="domains-title" className="flex flex-col gap-4">
      <div>
        <h2 id="domains-title" className="font-display text-xl font-semibold">
          Dziedziny atlasu
        </h2>
        <p className="text-sm text-text-muted">
          Każda dziedzina pracy spółdzielni (np. pszczelarstwo, ogrodnictwo) ma własny słownik etykiet, który prowadzą eksperci.
          Zarchiwizowana dziedzina zachowuje zdjęcia i etykiety, ale nie przyjmuje nowych.
        </p>
      </div>
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm sm:flex-row sm:items-end" aria-label="Nowa dziedzina">
          <label className="flex flex-1 flex-col gap-1">
            Nazwa
            <input className={INPUT} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="flex flex-[2] flex-col gap-1">
            Opis
            <input className={INPUT} maxLength={1000} value={description} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <Button type="submit" busy={create.isPending}>
            Dodaj dziedzinę
          </Button>
        </form>
        {error && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {error}
          </p>
        )}
      </Card>
      {domains.data?.length === 0 && <p className="text-text-muted">Nie ma jeszcze żadnej dziedziny.</p>}
      <ul className="flex flex-col gap-2" aria-label="Dziedziny">
        {domains.data?.map((d) => (
          <li key={d.id}>
            <DomainRow domain={d} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function DomainRow({ domain }: { domain: Domain }) {
  const update = useUpdateDomain();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(domain.name);
  const archived = domain.status === "ARCHIVED";

  async function save(patch: { name?: string; status?: Domain["status"] }) {
    try {
      await update.mutateAsync({ id: domain.id, ...patch });
      setEditing(false);
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else toast(atlasError(e), "error");
    }
  }

  return (
    <Card className="flex flex-wrap items-center justify-between gap-3">
      {editing ? (
        <form
          className="flex flex-1 gap-2"
          aria-label={`Zmień nazwę: ${domain.name}`}
          onSubmit={(e) => {
            e.preventDefault();
            void save({ name: name.trim() });
          }}
        >
          <input className={`${INPUT} flex-1`} aria-label="Nowa nazwa" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" busy={update.isPending}>
            Zapisz
          </Button>
          <Button variant="ghost" onClick={() => setEditing(false)}>
            Anuluj
          </Button>
        </form>
      ) : (
        <div className="min-w-0">
          <p className="font-medium">
            {domain.name} {archived && <Badge>Archiwum</Badge>}
          </p>
          {domain.description && <p className="text-sm text-text-muted">{domain.description}</p>}
        </div>
      )}
      {!editing && (
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Zmień nazwę
          </Button>
          <Button variant="ghost" busy={update.isPending} onClick={() => void save({ status: archived ? "ACTIVE" : "ARCHIVED" })}>
            {archived ? "Przywróć" : "Archiwizuj"}
          </Button>
        </div>
      )}
    </Card>
  );
}
