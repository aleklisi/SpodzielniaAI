"use client";

import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  CAPABILITIES,
  generates,
  llmError,
  useClearProvider,
  useCreateModel,
  useCreateProvider,
  useModelDisabled,
  useProbe,
  useProviderDisabled,
  useTestProvider,
  useUnclearProvider,
  useUpdateProvider,
  type Capability,
  type Connection,
  type LlmModel,
  type LlmStatus,
  type Provider,
} from "@/lib/llm-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";

/** What the backend will classify ``url`` as (it decides; this only explains up front). */
export function previewKind(url: string, status: Pick<LlmStatus, "local_endpoints" | "cloud_hosts">): "LOCAL" | "CLOUD" | null {
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null;
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
  if (status.local_endpoints.includes(parsed.origin)) return "LOCAL";
  if (parsed.protocol === "https:" && !parsed.port && status.cloud_hosts.includes(parsed.hostname)) return "CLOUD";
  return null;
}

export function KindBadge({ kind }: { kind: "LOCAL" | "CLOUD" }) {
  return kind === "LOCAL" ? <Badge tone="ok">Lokalny</Badge> : <Badge tone="warn">Chmura</Badge>;
}

function keyLabel(provider: Provider): string {
  if (provider.inherit_env_key) return "z konfiguracji serwera";
  if (provider.key_hint) return `••••${provider.key_hint}`;
  return provider.key_set ? "ustawiony" : "brak";
}

// --- Providers ----------------------------------------------------------------------------------

export function ProvidersPanel({ status, providers }: { status: LlmStatus; providers: Provider[] }) {
  const [editing, setEditing] = useState<Provider | "new" | null>(null);
  const [clearing, setClearing] = useState<Provider | null>(null);
  const [unclearing, setUnclearing] = useState<Provider | null>(null);
  const [tests, setTests] = useState<Record<string, Connection | string>>({});
  const test = useTestProvider();
  const disable = useProviderDisabled();
  const toast = useToast();

  async function runTest(provider: Provider) {
    try {
      const result = await test.mutateAsync(provider.id);
      setTests((t) => ({ ...t, [provider.id]: result }));
    } catch (error) {
      setTests((t) => ({ ...t, [provider.id]: llmError(error, "Test nie powiódł się.") }));
    }
  }

  async function toggle(provider: Provider) {
    try {
      await disable.mutateAsync({ id: provider.id, disabled: !provider.disabled });
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-1 text-sm">
        <p>
          Dozwolone serwery lokalne: <code>{status.local_endpoints.join(", ") || "—"}</code>
        </p>
        <p>
          Dozwolone API chmurowe: <code>{status.cloud_hosts.join(", ") || "—"}</code>
          {!status.allow_cloud && " · chmura wyłączona w konfiguracji serwera"}
        </p>
        <p>
          Mogą otrzymywać dane prywatne: <code>{status.private_data_endpoints.join(", ") || "—"}</code>
          {status.private_data_endpoints.length === 0 && " · funkcje na danych prywatnych są wyłączone"}
        </p>
        <p className="text-text-muted">Listy ustawia administrator serwera (.env); panel nie może ich rozszerzyć.</p>
      </Card>
      <div className="flex justify-end">
        <Button onClick={() => setEditing("new")}>Dodaj dostawcę</Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-text-muted">
            <tr>
              <th className="py-2 pr-3">Nazwa</th>
              <th className="py-2 pr-3">Rodzaj</th>
              <th className="py-2 pr-3">Adres</th>
              <th className="py-2 pr-3">Klucz API</th>
              <th className="py-2 pr-3">Dane prywatne</th>
              <th className="py-2 pr-3">Stan</th>
              <th className="py-2">Akcje</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => {
              const result = tests[p.id];
              return (
                <tr key={p.id} className="border-t border-border align-top">
                  <td className="py-2 pr-3 font-medium">{p.name}</td>
                  <td className="py-2 pr-3">
                    <KindBadge kind={p.kind} />
                  </td>
                  <td className="py-2 pr-3">
                    <code className="break-all">{p.base_url}</code>
                  </td>
                  <td className="py-2 pr-3">{keyLabel(p)}</td>
                  <td className="py-2 pr-3">
                    <ClearanceCell provider={p} onClear={() => setClearing(p)} onUnclear={() => setUnclearing(p)} />
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {p.in_active && <Badge tone="primary">W aktywnej konfiguracji</Badge>}
                      {p.disabled && <Badge>Wyłączony</Badge>}
                      {!p.allowed && <Badge tone="danger">Poza listą dozwolonych</Badge>}
                    </div>
                    {result !== undefined && <ConnectionResult result={result} />}
                  </td>
                  <td className="py-2">
                    <div className="flex flex-wrap gap-1">
                      <Button
                        variant="secondary"
                        busy={test.isPending && test.variables === p.id}
                        disabled={p.kind === "CLOUD" && !status.allow_cloud}
                        onClick={() => void runTest(p)}
                      >
                        Testuj połączenie
                      </Button>
                      <Button variant="ghost" onClick={() => setEditing(p)}>
                        Edytuj
                      </Button>
                      <Button variant="ghost" disabled={p.in_active && !p.disabled} onClick={() => void toggle(p)}>
                        {p.disabled ? "Włącz" : "Wyłącz"}
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <ProviderDialog status={status} provider={editing} onClose={() => setEditing(null)} />
      <ClearanceDialog provider={clearing} onClose={() => setClearing(null)} />
      <UnclearDialog provider={unclearing} onClose={() => setUnclearing(null)} />
    </div>
  );
}

// --- Clearance for private data (10.1) -----------------------------------------------------------

export function ClearanceCell({ provider, onClear, onUnclear }: { provider: Provider; onClear: () => void; onUnclear: () => void }) {
  if (provider.private_data_cleared) {
    return (
      <div className="flex flex-col items-start gap-1">
        <Badge tone="ok">Dopuszczony do danych prywatnych</Badge>
        <span className="text-xs text-text-muted">
          {provider.cleared_region} · przechowywanie: {provider.cleared_retention} · {provider.cleared_contract_ref}
        </span>
        <Button variant="ghost" onClick={onUnclear}>
          Cofnij dopuszczenie
        </Button>
      </div>
    );
  }
  if (!provider.clearable) {
    return <span className="text-xs text-text-muted">Poza listą serwera dla danych prywatnych</span>;
  }
  return (
    <Button variant="secondary" disabled={provider.disabled} onClick={onClear}>
      Dopuść do danych prywatnych
    </Button>
  );
}

function ClearanceDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  return (
    <Dialog open={provider !== null} onClose={onClose} title={`Dopuszczenie do danych prywatnych: ${provider?.name ?? ""}`}>
      {provider !== null && <ClearanceForm key={provider.id} provider={provider} onDone={onClose} />}
    </Dialog>
  );
}

export function ClearanceForm({ provider, onDone }: { provider: Provider; onDone: () => void }) {
  const [region, setRegion] = useState("");
  const [retention, setRetention] = useState("");
  const [contract, setContract] = useState("");
  const clear = useClearProvider();
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await clear.mutateAsync({ id: provider.id, clearance: { region, retention, contract_ref: contract } });
      toast(`${provider.name} może teraz przetwarzać dane prywatne.`, "ok");
      onDone();
    } catch (error) {
      if (!(error instanceof StepUpRequired)) toast(llmError(error), "error");
    }
  }

  if (clear.error instanceof StepUpRequired) {
    return (
      <StepUpNotice
        stepUp={clear.error.stepUp}
        reason="Dopuszczenie dostawcy do danych prywatnych wymaga niedawnego logowania. Zaloguj się ponownie i powtórz operację."
      />
    );
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
      <p>
        Prywatne notatki, anonimizacja, szkice do Wiki, opisy zdjęć i embeddingi będą mogły trafiać do{" "}
        <code className="break-all">{provider.base_url}</code>
        {provider.kind === "CLOUD" ? " — to serwer zewnętrzny, poza spółdzielnią." : "."}
      </p>
      <p className="text-text-muted">
        Członkowie zobaczą te informacje na stronie „Gdzie przetwarzane są Twoje dane”. Dopuszczaj tylko dostawców, z którymi
        spółdzielnia ma umowę powierzenia danych (lub własne serwery).
      </p>
      <label className="flex flex-col gap-1">
        Region przetwarzania
        <input className={INPUT} required minLength={2} maxLength={64} value={region} onChange={(e) => setRegion(e.target.value)} placeholder="np. UE (Francja)" />
      </label>
      <label className="flex flex-col gap-1">
        Przechowywanie danych przez dostawcę
        <input className={INPUT} required minLength={2} maxLength={64} value={retention} onChange={(e) => setRetention(e.target.value)} placeholder="np. brak przechowywania, 30 dni" />
      </label>
      <label className="flex flex-col gap-1">
        Umowa (numer lub odnośnik)
        <input className={INPUT} required minLength={2} maxLength={200} value={contract} onChange={(e) => setContract(e.target.value)} placeholder="np. DPA 2026/01" />
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          Anuluj
        </Button>
        <Button type="submit" busy={clear.isPending}>
          Dopuść
        </Button>
      </div>
    </form>
  );
}

function UnclearDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const unclear = useUnclearProvider();
  const toast = useToast();

  async function confirm() {
    if (!provider) return;
    try {
      await unclear.mutateAsync(provider.id);
      toast(`${provider.name} nie otrzymuje już danych prywatnych.`, "ok");
      onClose();
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <Dialog open={provider !== null} onClose={onClose} title={`Cofnij dopuszczenie: ${provider?.name ?? ""}`}>
      <div className="flex flex-col gap-3 text-sm">
        <p>Dane prywatne przestaną trafiać do tego dostawcy od razu.</p>
        {provider?.in_active && (
          <p className="text-warn">
            Dostawca obsługuje aktywną konfigurację: funkcje na danych prywatnych, które z niego korzystają, zatrzymają się, dopóki
            nie przypiszesz im innego dopuszczonego modelu. System nie przełączy ich na innego dostawcę sam.
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button variant="danger" busy={unclear.isPending} onClick={() => void confirm()}>
            Cofnij dopuszczenie
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ConnectionResult({ result }: { result: Connection | string }) {
  if (typeof result === "string") return <p className="mt-1 text-danger">{result}</p>;
  if (!result.ok) {
    return (
      <p className="mt-1 text-danger" role="status">
        Brak połączenia{result.status ? ` (HTTP ${result.status})` : result.error ? ` (${result.error})` : ""}
      </p>
    );
  }
  return (
    <p className="mt-1 text-ok" role="status">
      Połączono ({result.latency_ms} ms) · modele: {result.models?.length ? result.models.join(", ") : "brak listy"}
    </p>
  );
}

function ProviderDialog({ status, provider, onClose }: { status: LlmStatus; provider: Provider | "new" | null; onClose: () => void }) {
  const isNew = provider === "new";
  const current = provider !== "new" ? provider : null;
  return (
    <Dialog open={provider !== null} onClose={onClose} title={isNew ? "Nowy dostawca" : `Edycja: ${current?.name ?? ""}`}>
      {provider !== null && <ProviderForm key={current?.id ?? "new"} status={status} current={current} onDone={onClose} />}
    </Dialog>
  );
}

function ProviderForm({ status, current, onDone }: { status: LlmStatus; current: Provider | null; onDone: () => void }) {
  const [name, setName] = useState(current?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(current?.base_url ?? "");
  const [apiKey, setApiKey] = useState(""); // never pre-filled: keys are write-only
  const [clearKey, setClearKey] = useState(false);
  const [structured, setStructured] = useState(current?.structured_output ?? "");
  const [timeout, setTimeout_] = useState(current?.timeout_seconds ? String(current.timeout_seconds) : "");
  const create = useCreateProvider();
  const update = useUpdateProvider();
  const toast = useToast();
  const kind = previewKind(baseUrl, status);
  const cloudBlocked = kind === "CLOUD" && !status.allow_cloud;
  const error = create.error ?? update.error;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const structured_output = structured === "json_schema" || structured === "prompt" ? structured : null;
    const timeout_seconds = timeout ? Number(timeout) : null;
    try {
      if (current) {
        await update.mutateAsync({
          id: current.id,
          patch: {
            base_url: baseUrl.trim(),
            structured_output,
            timeout_seconds,
            ...(apiKey ? { api_key: apiKey } : {}),
            clear_api_key: clearKey,
          },
        });
      } else {
        await create.mutateAsync({
          name: name.trim(),
          base_url: baseUrl.trim(),
          litellm_provider: "openai",
          api_key: apiKey || null,
          structured_output,
          timeout_seconds,
        });
      }
      toast("Zapisano dostawcę.", "ok");
      onDone();
    } catch {
      // shown below
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
      {!current && (
        <label className="flex flex-col gap-1">
          Nazwa (małe litery, cyfry, „.”, „_”, „-”)
          <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} pattern="[a-z0-9][a-z0-9._\-]{0,63}" required />
        </label>
      )}
      <label className="flex flex-col gap-1">
        Adres API zgodnego z OpenAI
        <input
          className={INPUT}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://llm:11434/v1"
          required
          aria-describedby="kind-hint"
        />
      </label>
      <p id="kind-hint" role="status" className="text-xs">
        {baseUrl.trim() === "" ? null : kind === "LOCAL" ? (
          <span className="text-ok">Serwer lokalny: dane nie opuszczają infrastruktury spółdzielni.</span>
        ) : kind === "CLOUD" ? (
          cloudBlocked ? (
            <span className="text-danger">
              API chmurowe, ale chmura jest wyłączona w konfiguracji serwera (LLM_ALLOW_CLOUD). Dostawcy nie można dodać z panelu.
            </span>
          ) : (
            <span className="text-warn">API chmurowe: nadaje się tylko do czatu spółdzielni (RAG) i playgroundu.</span>
          )
        ) : (
          <span className="text-danger">Adres spoza listy dozwolonych serwerów.</span>
        )}
      </p>
      <label className="flex flex-col gap-1">
        Klucz API {current?.key_set && <span className="text-text-muted">(zapisany: {keyLabel(current)})</span>}
        <input
          className={INPUT}
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={current ? "pozostaw puste, aby nie zmieniać" : "opcjonalny"}
        />
      </label>
      {current?.key_set && !current.inherit_env_key && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={clearKey} onChange={(e) => setClearKey(e.target.checked)} />
          Usuń zapisany klucz
        </label>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          Wymuszanie JSON
          <select className={INPUT} value={structured} onChange={(e) => setStructured(e.target.value)}>
            <option value="">domyślne serwera</option>
            <option value="json_schema">schemat (response_format)</option>
            <option value="prompt">schemat w poleceniu</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Limit czasu [s]
          <input className={INPUT} type="number" min={1} max={600} value={timeout} onChange={(e) => setTimeout_(e.target.value)} />
        </label>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {llmError(error)}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>
          Anuluj
        </Button>
        <Button type="submit" disabled={kind === null || cloudBlocked} busy={create.isPending || update.isPending}>
          Zapisz
        </Button>
      </div>
    </form>
  );
}

// --- Models -------------------------------------------------------------------------------------

const CHECKS: Record<string, string> = {
  reply: "Odpowiedź",
  json_json_schema: "JSON (response_format)",
  json_prompt: "JSON (schemat w poleceniu)",
  image: "Opis obrazu",
  embed: "Embedding",
  transcribe: "Transkrypcja nagrania testowego",
  ocr: "Odczyt tekstu z obrazka",
  image_embed: "Embeddingi zdjęć (ten sam obiekt bliżej niż inny)",
};

interface ProbeCheck {
  name: string;
  ok: boolean;
  latency_ms?: number;
  tokens?: number | null;
  error?: string;
  dimension?: number;
  cosine_unrelated?: number;
  word_error_rate?: number;
}

export function ProbeReport({ report }: { report: Record<string, unknown> }) {
  const checks = (Array.isArray(report.checks) ? report.checks : []) as ProbeCheck[];
  const recommended = report.recommended_structured_output as string | null | undefined;
  return (
    <div className="flex flex-col gap-1 text-xs" aria-label="Wynik testu">
      <ul className="flex flex-col gap-0.5">
        {checks.map((c) => (
          <li key={c.name} className={c.ok ? "text-ok" : "text-danger"}>
            {c.ok ? "✓" : "✗"} {CHECKS[c.name] ?? c.name}
            {c.latency_ms !== undefined && ` · ${c.latency_ms} ms`}
            {c.tokens ? ` · ${c.tokens} tokenów` : ""}
            {c.dimension !== undefined && ` · wymiar ${c.dimension}`}
            {c.cosine_unrelated !== undefined && ` · podobieństwo niepowiązanych ${c.cosine_unrelated}`}
            {c.word_error_rate !== undefined && ` · błędne słowa ${Math.round(c.word_error_rate * 100)}%`}
            {c.error && ` · ${c.error}`}
          </li>
        ))}
      </ul>
      {recommended !== undefined && (
        <p>
          Zalecany tryb JSON:{" "}
          <strong>{recommended === "json_schema" ? "response_format" : recommended === "prompt" ? "schemat w poleceniu" : "żaden nie działa"}</strong>
        </p>
      )}
      {report.dimension_matches === false && (
        <p className="text-danger">Wymiar nie zgadza się z indeksem ({String(report.expected_dimension)}).</p>
      )}
    </div>
  );
}

export function ModelsPanel({ status, providers, models }: { status: LlmStatus; providers: Provider[]; models: LlmModel[] }) {
  const probe = useProbe();
  const disable = useModelDisabled();
  const toast = useToast();
  const byProvider = new Map(providers.map((p) => [p.id, p]));

  async function runProbe(model: LlmModel) {
    try {
      const result = await probe.mutateAsync(model.id);
      toast(result.report.ok ? "Test modelu zakończony powodzeniem." : "Model nie przeszedł testu.", result.report.ok ? "ok" : "error");
    } catch (error) {
      toast(llmError(error, "Test nie powiódł się."), "error");
    }
  }

  async function toggle(model: LlmModel) {
    try {
      await disable.mutateAsync({ id: model.id, disabled: !model.disabled });
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <AddModel status={status} providers={providers} />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-text-muted">
            <tr>
              <th className="py-2 pr-3">Model</th>
              <th className="py-2 pr-3">Typ</th>
              <th className="py-2 pr-3">Parametry</th>
              <th className="py-2 pr-3">Ostatni test</th>
              <th className="py-2">Akcje</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const provider = byProvider.get(m.provider_id);
              const cloudBlocked = provider?.kind === "CLOUD" && !status.allow_cloud;
              return (
                <tr key={m.id} className="border-t border-border align-top">
                  <td className="py-2 pr-3">
                    <div className="font-medium">
                      {provider?.name}/{m.model_name}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {provider && <KindBadge kind={provider.kind} />}
                      {m.in_active && <Badge tone="primary">W aktywnej konfiguracji</Badge>}
                      {m.disabled && <Badge>Wyłączony</Badge>}
                    </div>
                  </td>
                  <td className="py-2 pr-3">{CAPABILITIES[m.capability]}</td>
                  <td className="py-2 pr-3 text-xs text-text-muted">
                    {m.capability === "EMBED" || m.capability === "IMAGE_EMBED"
                      ? `wymiar ${m.embed_dimension ?? "?"}`
                      : m.capability === "TRANSCRIBE"
                        ? "nagrania po polsku"
                        : `temp. ${m.temperature ?? "dom."} · max ${m.max_tokens ?? "dom."} tok.`}
                  </td>
                  <td className="py-2 pr-3">
                    {m.last_probe_at ? (
                      <>
                        <span className={m.last_probe_ok ? "text-ok" : "text-danger"}>
                          {m.last_probe_ok ? "Zaliczony" : "Niezaliczony"}
                        </span>{" "}
                        <span className="text-xs text-text-muted">{new Date(m.last_probe_at).toLocaleString("pl-PL")}</span>
                        {m.last_probe_report && <ProbeReport report={m.last_probe_report} />}
                      </>
                    ) : (
                      <span className="text-text-muted">Nie testowano</span>
                    )}
                  </td>
                  <td className="py-2">
                    <div className="flex flex-wrap gap-1">
                      <Button
                        variant="secondary"
                        busy={probe.isPending && probe.variables === m.id}
                        disabled={cloudBlocked || m.disabled}
                        onClick={() => void runProbe(m)}
                      >
                        Testuj model
                      </Button>
                      <Button variant="ghost" disabled={m.in_active && !m.disabled} onClick={() => void toggle(m)}>
                        {m.disabled ? "Włącz" : "Wyłącz"}
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AddModel({ status, providers }: { status: LlmStatus; providers: Provider[] }) {
  const usable = providers.filter((p) => !p.disabled && p.allowed);
  const [providerId, setProviderId] = useState(usable[0]?.id ?? "");
  const [name, setName] = useState("");
  const [capability, setCapability] = useState<Capability>("CHAT");
  const [temperature, setTemperature] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [discovered, setDiscovered] = useState<string[]>([]);
  const create = useCreateModel();
  const test = useTestProvider();
  const toast = useToast();
  const provider = providers.find((p) => p.id === providerId);

  async function discover() {
    try {
      const result = await test.mutateAsync(providerId);
      setDiscovered(result.models ?? []);
      if (!result.ok) toast("Serwer nie zwrócił listy modeli.", "error");
    } catch (error) {
      toast(llmError(error, "Nie udało się pobrać listy modeli."), "error");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await create.mutateAsync({
        provider_id: providerId,
        model_name: name.trim(),
        capability,
        temperature: generates(capability) && temperature ? Number(temperature) : null,
        max_tokens: generates(capability) && maxTokens ? Number(maxTokens) : null,
        embed_dimension: null, // the probe records it; each index version keeps its own

        notes: null,
      });
      toast("Dodano model. Przetestuj go przed użyciem.", "ok");
      setName("");
    } catch (error) {
      toast(llmError(error), "error");
    }
  }

  return (
    <Card>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Dodaj model">
        <h2 className="font-display text-lg font-semibold">Dodaj model</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="flex flex-col gap-1">
            Dostawca
            <select className={INPUT} value={providerId} onChange={(e) => setProviderId(e.target.value)}>
              {usable.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.kind === "LOCAL" ? "lokalny" : "chmura"})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            Nazwa modelu na serwerze
            <input className={INPUT} list="discovered-models" value={name} onChange={(e) => setName(e.target.value)} required />
            <datalist id="discovered-models">
              {discovered.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </label>
          <label className="flex flex-col gap-1">
            Typ
            <select className={INPUT} value={capability} onChange={(e) => setCapability(e.target.value as Capability)}>
              {(Object.keys(CAPABILITIES) as Capability[]).map((c) => (
                <option key={c} value={c}>
                  {CAPABILITIES[c]}
                </option>
              ))}
            </select>
          </label>
          {generates(capability) ? (
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1">
                Temperatura
                <input className={INPUT} type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                Max tokenów
                <input className={INPUT} type="number" min={1} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              </label>
            </div>
          ) : capability === "EMBED" || capability === "IMAGE_EMBED" ? (
            <p className="self-end text-xs text-text-muted">
              Wymiar wektora odczyta test modelu. Zmiana modelu embeddingów buduje nową wersję indeksu w tle.
            </p>
          ) : (
            <p className="self-end text-xs text-text-muted">
              Serwer z API /audio/transcriptions (np. whisper). Test modelu odczyta krótkie polskie nagranie testowe.
            </p>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            disabled={!provider || (provider.kind === "CLOUD" && !status.allow_cloud)}
            busy={test.isPending}
            onClick={() => void discover()}
          >
            Wykryj modele
          </Button>
          <Button type="submit" disabled={!providerId || !name.trim()} busy={create.isPending}>
            Dodaj
          </Button>
        </div>
      </form>
    </Card>
  );
}
