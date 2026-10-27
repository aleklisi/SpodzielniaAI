"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  atlasErrorKey,
  LABEL_STATUS_TONE,
  type AtlasErrorKey,
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

/** "Label vocabulary": the experts' view of one domain's labels, proposals first. */
export function LabelVocabulary() {
  const t = useTranslations("atlas");
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
      toast(t(`vocabulary.done.${kind}`), "ok");
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else toast(t(`errors.${atlasErrorKey(e)}`), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={t("vocabulary.title")}
        description={
          <>
            {t("vocabulary.description")}{" "}
            <Link href="/review" className="underline">
              {t("vocabulary.backToReview")}
            </Link>
          </>
        }
        actions={
          <Button onClick={() => setEditing("new")} disabled={!domainId}>
            <Plus aria-hidden className="size-4" />
            {t("vocabulary.newLabel")}
          </Button>
        }
      />
      {domains.data?.length === 0 && (
        <Card className="text-text-muted">{t("vocabulary.noDomains")}</Card>
      )}
      <div className="flex flex-wrap gap-3 text-sm">
        <label className="flex flex-col gap-1">
          {t("vocabulary.domain")}
          <select className={INPUT} value={domainId ?? ""} onChange={(e) => setChosen(e.target.value)}>
            {domains.data?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
                {d.status === "ARCHIVED" ? t("vocabulary.archivedSuffix") : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("vocabulary.status")}
          <select className={INPUT} value={status} onChange={(e) => setStatus(e.target.value as LabelStatus | "")}>
            <option value="">{t("vocabulary.all")}</option>
            {(Object.keys(LABEL_STATUS_TONE) as LabelStatus[]).map((s) => (
              <option key={s} value={s}>
                {t(`labelStatus.${s}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("vocabulary.search")}
          <input className={INPUT} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("vocabulary.placeholder")} />
        </label>
      </div>
      {labels.isError && !(labels.error instanceof StepUpRequired) && (
        <p role="alert" className="text-danger">
          {t("vocabulary.loadFailed")}
        </p>
      )}
      {labels.data?.length === 0 && <p className="text-text-muted">{t("vocabulary.empty")}</p>}
      <ul className="flex flex-col gap-2" aria-label={t("vocabulary.labels")}>
        {labels.data?.map((label) => (
          <li key={label.id}>
            <Card className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">
                  {label.name} <Badge tone={LABEL_STATUS_TONE[label.status]}>{t(`labelStatus.${label.status}`)}</Badge>
                </p>
                {label.description && <p className="text-sm text-text-muted">{label.description}</p>}
                {label.synonyms.length > 0 && <p className="text-xs text-text-muted">{t("vocabulary.synonyms", { list: label.synonyms.join(", ") })}</p>}
                {label.status === "PROPOSED" && (
                  <p className="text-xs text-text-muted">{t("vocabulary.proposedBy", { name: label.proposed_by_name ?? t("vocabulary.member") })}</p>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {label.status === "PROPOSED" && (
                  <>
                    <Button onClick={() => void run(label, "approve")}>{t("vocabulary.approve")}</Button>
                    <Button variant="secondary" onClick={() => setMerging(label)}>
                      {t("vocabulary.mergeWith")}
                    </Button>
                    <Button variant="ghost" onClick={() => void run(label, "reject")}>
                      {t("vocabulary.reject")}
                    </Button>
                  </>
                )}
                {label.status === "ACTIVE" && (
                  <>
                    <Button variant="secondary" onClick={() => setEditing(label)}>
                      {t("vocabulary.edit")}
                    </Button>
                    <Button variant="secondary" onClick={() => setMerging(label)}>
                      {t("vocabulary.mergeWith")}
                    </Button>
                    <Button variant="ghost" onClick={() => void run(label, "deprecate")}>
                      {t("vocabulary.deprecate")}
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
  const t = useTranslations("atlas");
  const common = useTranslations("common");
  const [name, setName] = useState(label?.name ?? "");
  const [description, setDescription] = useState(label?.description ?? "");
  const [synonyms, setSynonyms] = useState(label?.synonyms.join(", ") ?? "");
  const [error, setError] = useState<AtlasErrorKey | null>(null);
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
      else setError(atlasErrorKey(e));
    }
  }

  return (
    <Dialog open onClose={onClose} title={label ? t("vocabulary.editTitle") : t("vocabulary.newLabel")}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label={t("vocabulary.form")}>
        <label className="flex flex-col gap-1">
          {t("vocabulary.name")}
          <input className={INPUT} required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          {t("vocabulary.labelDescription")}
          <textarea className={INPUT} maxLength={1000} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          {t("vocabulary.synonymsField")}
          <input className={INPUT} value={synonyms} onChange={(e) => setSynonyms(e.target.value)} />
        </label>
        {error && (
          <p role="alert" className="text-danger">
            {t(`errors.${error}`)}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" busy={create.isPending || edit.isPending}>
            {common("save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function MergeDialog({ domainId, label, onClose }: { domainId: string; label: CuratedLabel; onClose: () => void }) {
  const t = useTranslations("atlas");
  const common = useTranslations("common");
  const active = useCuratedLabels(domainId, "", "ACTIVE");
  const [into, setInto] = useState("");
  const [error, setError] = useState<AtlasErrorKey | null>(null);
  const merge = useMergeLabel();
  const targets = (active.data ?? []).filter((l) => l.id !== label.id);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await merge.mutateAsync({ id: label.id, into });
      onClose();
    } catch (e) {
      if (e instanceof StepUpRequired) window.location.assign(e.stepUp);
      else setError(atlasErrorKey(e));
    }
  }

  return (
    <Dialog open onClose={onClose} title={t("vocabulary.mergeTitle", { name: label.name })}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label={t("vocabulary.mergeForm")}>
        <p className="text-text-muted">{t("vocabulary.mergeBody", { name: label.name })}</p>
        <label className="flex flex-col gap-1">
          {t("vocabulary.target")}
          <select className={INPUT} required value={into} onChange={(e) => setInto(e.target.value)}>
            <option value="">{t("vocabulary.choose")}</option>
            {targets.map((target) => (
              <option key={target.id} value={target.id}>
                {target.name}
              </option>
            ))}
          </select>
        </label>
        {error && (
          <p role="alert" className="text-danger">
            {t(`errors.${error}`)}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" busy={merge.isPending} disabled={!into}>
            {t("vocabulary.merge")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
