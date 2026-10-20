"use client";

import { useState, type FormEvent } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  llmError,
  modelLabel,
  usePlayground,
  usePresets,
  type LlmModel,
  type LlmStatus,
  type PlaygroundResult,
  type Provider,
} from "@/lib/llm-admin";

const MAX_MODELS = 3;

/** Up to three models answer the same prompt. No knowledge base, nothing stored. */
export function PlaygroundPanel({ status, providers, models }: { status: LlmStatus; providers: Provider[]; models: LlmModel[] }) {
  const presets = usePresets();
  const run = usePlayground();
  const [chosen, setChosen] = useState<string[]>([]);
  const [mode, setMode] = useState<"preset" | "own">("preset");
  const [preset, setPreset] = useState("pl_qa");
  const [prompt, setPrompt] = useState("");
  const [results, setResults] = useState<PlaygroundResult[]>([]);

  const byProvider = new Map(providers.map((p) => [p.id, p]));
  const candidates = models.filter((m) => {
    const p = byProvider.get(m.provider_id);
    return m.capability !== "EMBED" && !m.disabled && p !== undefined && !p.disabled && p.allowed;
  });
  const current = presets.data?.find((p) => p.key === preset);
  const isCloud = (m: LlmModel) => byProvider.get(m.provider_id)?.kind === "CLOUD";

  function toggle(id: string) {
    setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : c.length < MAX_MODELS ? [...c, id] : c));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const out = await run.mutateAsync(mode === "preset" ? { model_ids: chosen, preset } : { model_ids: chosen, prompt });
      setResults(out.results);
    } catch {
      setResults([]);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-text-muted">
        Playground nie korzysta z bazy wiedzy ani z notatek: modele widzą tylko to polecenie. Odpowiedzi nie są zapisywane; w
        dzienniku audytu zostaje skrót polecenia.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-4 text-sm">
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 font-medium">Modele (do {MAX_MODELS})</legend>
          <div className="flex flex-wrap gap-3">
            {candidates.map((m) => {
              const cloudOff = isCloud(m) && !status.allow_cloud;
              const localOnly = mode === "preset" && current?.local_only && isCloud(m);
              return (
                <label key={m.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={chosen.includes(m.id)}
                    disabled={cloudOff || Boolean(localOnly) || (!chosen.includes(m.id) && chosen.length >= MAX_MODELS)}
                    onChange={() => toggle(m.id)}
                  />
                  {modelLabel(m, providers)}
                  {isCloud(m) && <KindBadge kind="CLOUD" />}
                </label>
              );
            })}
          </div>
        </fieldset>
        <div className="flex gap-4" role="radiogroup" aria-label="Polecenie">
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "preset"} onChange={() => setMode("preset")} /> Gotowy scenariusz
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "own"} onChange={() => setMode("own")} /> Własne polecenie
          </label>
        </div>
        {mode === "preset" ? (
          <div className="flex flex-col gap-1">
            <label htmlFor="playground-preset">Scenariusz</label>
            <select
              id="playground-preset"
              className="rounded-lg border border-border bg-surface px-3 py-2"
              value={preset}
              onChange={(e) => {
                setPreset(e.target.value);
                if (presets.data?.find((p) => p.key === e.target.value)?.local_only) {
                  setChosen((c) => c.filter((id) => !isCloud(models.find((m) => m.id === id) as LlmModel)));
                }
              }}
            >
              {presets.data?.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.title}
                  {p.local_only ? " (tylko lokalnie)" : ""}
                </option>
              ))}
            </select>
            {current && <span className="text-xs text-text-muted">{current.prompt}</span>}
          </div>
        ) : (
          <label className="flex flex-col gap-1">
            Polecenie (nie wklejaj danych osobowych ani treści notatek)
            <textarea
              rows={4}
              maxLength={4000}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-2"
            />
          </label>
        )}
        {run.error && (
          <p role="alert" className="text-danger">
            {llmError(run.error)}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" busy={run.isPending} disabled={chosen.length === 0 || (mode === "own" && !prompt.trim())}>
            Uruchom
          </Button>
        </div>
      </form>

      {results.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label="Wyniki">
          {results.map((r) => (
            <Card key={r.model_id} className="flex flex-col gap-2 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-medium">{r.name}</h3>
                <KindBadge kind={r.local ? "LOCAL" : "CLOUD"} />
              </div>
              <p className="text-xs text-text-muted">
                {r.latency_ms !== null && r.latency_ms !== undefined ? `${(r.latency_ms / 1000).toFixed(1)} s` : ""}
                {r.tokens ? ` · ${r.tokens} tokenów` : ""}
                {r.json_valid === true ? " · poprawny JSON" : r.json_valid === false ? " · niepoprawny JSON" : ""}
              </p>
              {r.ok ? (
                // Plain text on purpose: model output is never rendered as HTML or Markdown here.
                <pre className="whitespace-pre-wrap break-words font-sans text-sm">{r.content}</pre>
              ) : (
                <p className="text-danger">Błąd: {r.error}</p>
              )}
              {r.truncated && <p className="text-xs text-text-muted">Odpowiedź skrócona.</p>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
