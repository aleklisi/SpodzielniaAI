"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { KindBadge } from "@/components/admin/llm/Catalog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import {
  eligibleModels,
  llmErrorKey,
  modelLabel,
  probeFresh,
  PURPOSE_ORDER,
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
  const t = useTranslations("adminLlm");
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
      toast(t(`errors.${llmErrorKey(error)}`), "error");
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
            ? t("routing.draft", { number: draft.number, base: draft.based_on ?? "—" })
            : t("routing.active", { number: active?.number ?? "—" })}
        </p>
        <div className="flex gap-2">
          {draft ? (
            <>
              <Button variant="ghost" busy={deleteDraft.isPending} onClick={() => void run(() => deleteDraft.mutateAsync(draft.number))}>
                {t("routing.discard")}
              </Button>
              <Button disabled={pending.length === 0} onClick={() => setConfirming(true)}>
                {t("routing.activate", { number: draft.number })}
              </Button>
            </>
          ) : (
            <Button busy={createDraft.isPending} onClick={() => void run(() => createDraft.mutateAsync({}))}>
              {t("routing.newDraft")}
            </Button>
          )}
        </div>
      </div>

      <DataTable>
        <THead>
          <Tr>
            <Th>{t("routing.feature")}</Th>
            <Th>{t("routing.current")}</Th>
            {draft && <Th>{t("routing.inDraft")}</Th>}
            {draft && <Th>{t("routing.test")}</Th>}
          </Tr>
        </THead>
        <TBody>
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
                <Tr key={p.purpose}>
                  <Td primary>
                    <div className="font-medium">{t(`purposes.${p.purpose}.label`)}</div>
                    <div className="text-xs text-text-muted">{t(`purposes.${p.purpose}.hint`)}</div>
                    {p.private_data && (
                      <Badge tone="ok" className="mt-1" title={t("routing.privateHint")}>
                        {t("routing.private")}
                      </Badge>
                    )}
                  </Td>
                  <Td label={t("routing.current")}>
                    {current ? (
                      <span className="flex flex-wrap items-center gap-1">
                        {current.name} <KindBadge kind={current.kind} />
                      </span>
                    ) : (
                      <span className="text-text-muted">{t("routing.none")}</span>
                    )}
                  </Td>
                  {draft && (
                    <Td label={t("routing.inDraft")}>
                      <label className="sr-only" htmlFor={`route-${p.purpose}`}>
                        {t("routing.modelFor", { purpose: t(`purposes.${p.purpose}.label`) })}
                      </label>
                      <select
                        id={`route-${p.purpose}`}
                        className={`${INPUT} ${changed ? "border-primary" : ""}`}
                        value={selectedId}
                        disabled={setRouting.isPending}
                        onChange={(e) =>
                          void run(() =>
                            setRouting.mutateAsync({ number: draft.number, routes: { [p.purpose]: e.target.value || null } }),
                          )
                        }
                      >
                        <option value="">{t("routing.noModel")}</option>
                        {selected && !options.includes(selected) && <option value={selected.id}>{modelLabel(selected, providers)}</option>}
                        {options.map((m) => (
                          <option key={m.id} value={m.id}>
                            {modelLabel(m, providers)}
                            {providers.find((pr) => pr.id === m.provider_id)?.kind === "CLOUD" ? t("routing.cloudSuffix") : ""}
                          </option>
                        ))}
                      </select>
                      {(p.purpose === "EMBED" || p.purpose === "IMAGE_EMBED") && (
                        <p className="mt-1 text-xs text-text-muted">{t("routing.rebuildHint")}</p>
                      )}
                    </Td>
                  )}
                  {draft && (
                    <Td label={t("routing.test")}>
                      {selected &&
                        changed &&
                        (probeFresh(selected, status.probe_max_age_hours) ? (
                          <span className="text-ok">{t("routing.tested")}</span>
                        ) : (
                          <Button
                            variant="secondary"
                            busy={probe.isPending && probe.variables === selected.id}
                            onClick={() => void run(() => probe.mutateAsync(selected.id), t("routing.probeDone"))}
                          >
                            {t("routing.runTest")}
                          </Button>
                        ))}
                    </Td>
                  )}
                </Tr>
              );
            })}
        </TBody>
      </DataTable>

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
            }, t("routing.activated", { number: draft.number }))
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
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const cloud = list.filter((c) => c.cloud);
  return (
    <Dialog open={open} onClose={onClose} title={t("routing.confirmTitle", { number })}>
      <div className="flex flex-col gap-3 text-sm">
        <ul className="flex flex-col gap-1" aria-label={t("routing.changes")}>
          {list.map((c) => (
            <li key={c.purpose}>
              <strong>{t(`purposes.${c.purpose}.label`)}:</strong> {c.before} → {c.after}
            </li>
          ))}
        </ul>
        {cloud.length > 0 && (
          <Card role="alert" className="border-warn p-3">
            {t("routing.cloudWarning", { purposes: cloud.map((c) => t(`purposes.${c.purpose}.label`)).join(", ") })}
          </Card>
        )}
        {unprobed.length > 0 && (
          <p role="status" className="text-warn">
            {t("routing.unprobed", { models: unprobed.join(", ") })}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button busy={busy} onClick={onConfirm}>
            {t("routing.submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
