"use client";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { PURPOSE_TEXT, useProcessors, type Processor } from "@/lib/processors";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "long" });

function ProcessorCard({ p }: { p: Processor }) {
  return (
    <Card className="flex flex-col gap-2" aria-label={`Dostawca ${p.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-display text-lg font-semibold">{p.name}</h2>
        <Badge tone={p.on_server ? "primary" : "neutral"}>{p.on_server ? "Serwer spółdzielni" : "Chmura"}</Badge>
        <span className="text-xs text-text-muted">{p.host}</span>
      </div>
      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm break-words">
        <dt className="text-text-muted">Region</dt>
        <dd>{p.region ?? "—"}</dd>
        <dt className="text-text-muted">Przechowywanie</dt>
        <dd>{p.retention ?? "—"}</dd>
        <dt className="text-text-muted">Umowa</dt>
        <dd>{p.contract_ref ?? "—"}</dd>
        <dt className="text-text-muted">Dopuszczony od</dt>
        <dd>{p.cleared_since ? DATE.format(new Date(p.cleared_since)) : "—"}</dd>
      </dl>
      {p.purposes.length > 0 ? (
        <ul className="list-disc pl-5 text-sm">
          {p.purposes.map((purpose) => (
            <li key={purpose}>{PURPOSE_TEXT[purpose]}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-muted">Dopuszczony, ale obecnie nie przetwarza Twoich danych.</p>
      )}
    </Card>
  );
}

export function ProcessorsPage() {
  const processors = useProcessors();
  const data = processors.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Gdzie przetwarzane są Twoje dane"
        description="Twoje notatki, nagrania, zdjęcia i dokumenty trafiają do modeli AI wyłącznie u dostawców dopuszczonych do danych prywatnych przez administratora spółdzielni (w granicach ustalonych przez operatora serwera). Jeśli żaden dostawca nie jest dopuszczony do danej funkcji, ta funkcja jest wyłączona: nic nie trafia gdzie indziej."
      />
      {processors.isLoading && <p className="text-sm text-text-muted">Wczytywanie…</p>}
      {processors.isError && <p className="text-sm text-danger">Nie udało się wczytać listy.</p>}
      {data && (
        <>
          <div className="grid gap-4 @3xl:grid-cols-2">
            {data.processors.map((p) => (
              <ProcessorCard key={p.name} p={p} />
            ))}
          </div>
          {data.processors.length === 0 && (
            <p className="text-sm">Żaden dostawca nie jest dopuszczony do danych prywatnych: funkcje AI na Twoich danych są wyłączone.</p>
          )}
          {data.built_in.length > 0 && (
            <Card aria-label="Modele wbudowane">
              <h2 className="font-display text-lg font-semibold">Na serwerze spółdzielni (modele wbudowane)</h2>
              <ul className="list-disc pl-5 text-sm">
                {data.built_in.map((purpose) => (
                  <li key={purpose}>{PURPOSE_TEXT[purpose]}</li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
