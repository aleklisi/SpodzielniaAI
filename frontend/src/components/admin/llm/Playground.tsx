"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import {
  generates,
  llmErrorKey,
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
  const t = useTranslations("adminLlm");
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
    return generates(m.capability) && !m.disabled && p !== undefined && !p.disabled && p.allowed;
  });
  const current = presets.data?.find((p) => p.key === preset);
  const isCloud = (m: LlmModel) => byProvider.get(m.provider_id)?.kind === "CLOUD";
  const isCleared = (m: LlmModel) => byProvider.get(m.provider_id)?.private_data_cleared === true;

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
      <p className="text-sm text-text-muted">{t("playground.intro")}</p>
      <form onSubmit={submit} className="flex flex-col gap-4 text-sm">
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 font-medium">{t("playground.models", { max: MAX_MODELS })}</legend>
          <div className="flex flex-wrap gap-3">
            {candidates.map((m) => {
              const cloudOff = isCloud(m) && !status.allow_cloud;
              const notCleared = mode === "preset" && current?.private_data && !isCleared(m);
              return (
                <label key={m.id} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={chosen.includes(m.id)}
                    disabled={cloudOff || Boolean(notCleared) || (!chosen.includes(m.id) && chosen.length >= MAX_MODELS)}
                    onChange={() => toggle(m.id)}
                  />
                  {modelLabel(m, providers)}
                  {isCloud(m) && <KindBadge kind="CLOUD" />}
                </label>
              );
            })}
          </div>
        </fieldset>
        <div className="flex gap-4" role="radiogroup" aria-label={t("playground.prompt")}>
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "preset"} onChange={() => setMode("preset")} /> {t("playground.preset")}
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={mode === "own"} onChange={() => setMode("own")} /> {t("playground.own")}
          </label>
        </div>
        {mode === "preset" ? (
          <div className="flex flex-col gap-1">
            <label htmlFor="playground-preset">{t("playground.scenario")}</label>
            <select
              id="playground-preset"
              className="rounded-lg border border-border bg-surface px-3 py-2"
              value={preset}
              onChange={(e) => {
                setPreset(e.target.value);
                if (presets.data?.find((p) => p.key === e.target.value)?.private_data) {
                  setChosen((c) => c.filter((id) => isCleared(models.find((m) => m.id === id) as LlmModel)));
                }
              }}
            >
              {presets.data?.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.title}
                  {p.private_data ? t("playground.privateOnly") : ""}
                </option>
              ))}
            </select>
            {current && <span className="text-xs text-text-muted">{current.prompt}</span>}
          </div>
        ) : (
          <label className="flex flex-col gap-1">
            {t("playground.ownLabel")}
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
            {t(`errors.${llmErrorKey(run.error)}`)}
          </p>
        )}
        <div className="flex justify-end">
          <Button type="submit" busy={run.isPending} disabled={chosen.length === 0 || (mode === "own" && !prompt.trim())}>
            {t("playground.run")}
          </Button>
        </div>
      </form>

      {results.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3" aria-label={t("playground.results")}>
          {results.map((r) => (
            <Card key={r.model_id} className="flex flex-col gap-2 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-medium">{r.name}</h3>
                <KindBadge kind={r.local ? "LOCAL" : "CLOUD"} />
              </div>
              <p className="text-xs text-text-muted">
                {r.latency_ms !== null && r.latency_ms !== undefined ? `${(r.latency_ms / 1000).toFixed(1)} s` : ""}
                {r.tokens ? t("playground.tokens", { count: r.tokens }) : ""}
                {r.json_valid === true ? t("playground.jsonValid") : r.json_valid === false ? t("playground.jsonInvalid") : ""}
              </p>
              {r.ok ? (
                // Plain text on purpose: model output is never rendered as HTML or Markdown here.
                <pre className="whitespace-pre-wrap break-words font-sans text-sm">{r.content}</pre>
              ) : (
                <p className="text-danger">{t("playground.error", { error: r.error ?? "" })}</p>
              )}
              {r.truncated && <p className="text-xs text-text-muted">{t("playground.truncated")}</p>}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
