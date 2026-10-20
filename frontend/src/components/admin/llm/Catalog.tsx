"use client";

import { useState, type FormEvent } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import {
  CAPABILITIES,
  llmError,
  useCreateModel,
  useCreateProvider,
  useModelDisabled,
  useProbe,
  useProviderDisabled,
  useTestProvider,
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
    </div>
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
};

interface ProbeCheck {
  name: string;
  ok: boolean;
  latency_ms?: number;
  tokens?: number | null;
  error?: string;
  dimension?: number;
  cosine_unrelated?: number;
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
                    {m.capability === "EMBED"
                      ? `wymiar ${m.embed_dimension ?? "?"}`
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
        temperature: temperature ? Number(temperature) : null,
        max_tokens: maxTokens ? Number(maxTokens) : null,
        embed_dimension: capability === "EMBED" ? status.vector_size : null,
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
                <option key={c} value={c} disabled={c === "EMBED" && provider?.kind === "CLOUD"}>
                  {CAPABILITIES[c]}
                </option>
              ))}
            </select>
          </label>
          {capability !== "EMBED" ? (
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
          ) : (
            <p className="self-end text-xs text-text-muted">Wymiar musi wynosić {status.vector_size} (indeks Qdrant).</p>
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
