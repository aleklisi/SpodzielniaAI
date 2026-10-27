"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { StepUpRequired } from "@/lib/api";
import { useDomains, useLabels } from "@/lib/atlas";
import { downloadUrl, exportErrorKey, useCreateExport, useExports, type ExportErrorKey } from "@/lib/atlas-export";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2 text-sm";

/**
 * "Dataset export": approved photos whose authors allowed export, as COCO or YOLO. The
 * download link works once and is known only to this page (the token is shown once).
 */
export function ExportPanel() {
  const t = useTranslations("adminAtlas.export");
  const formatter = useFormatter();
  const exports = useExports();
  const domains = useDomains();
  const create = useCreateExport();
  const [domainId, setDomainId] = useState("");
  const [format, setFormat] = useState<"COCO" | "YOLO">("COCO");
  const [labelIds, setLabelIds] = useState<string[]>([]);
  const [since, setSince] = useState("");
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [error, setError] = useState<ExportErrorKey | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const labels = useLabels(domainId || null, "");

  if (exports.data && !exports.data.enabled) {
    return (
      <section aria-labelledby="export-title" className="flex flex-col gap-2">
        <h2 id="export-title" className="font-display text-xl font-semibold">
          {t("title")}
        </h2>
        <Card className="text-sm text-text-muted">{t.rich("disabled", { code: (chunks) => <code>{chunks}</code> })}</Card>
      </section>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await create.mutateAsync({
        format,
        domain_id: domainId,
        label_ids: labelIds.length ? labelIds : null,
        min_verified_at: since ? new Date(`${since}T00:00:00Z`).toISOString() : null,
      });
      setTokens((current) => ({ ...current, [created.export.id]: created.download_token }));
    } catch (e) {
      if (e instanceof StepUpRequired) setStepUp(e.stepUp);
      else setError(exportErrorKey(e));
    }
  }

  return (
    <section aria-labelledby="export-title" className="flex flex-col gap-4">
      <div>
        <h2 id="export-title" className="font-display text-xl font-semibold">
          {t("title")}
        </h2>
        <p className="text-sm text-text-muted">{t("intro")}</p>
      </div>
      {stepUp ? (
        <StepUpNotice stepUp={stepUp} reason={t("stepUp")} />
      ) : (
        <Card>
          <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label={t("new")}>
            <div className="flex flex-wrap gap-3">
              <label className="flex flex-col gap-1">
                {t("domain")}
                <select
                  className={INPUT}
                  required
                  value={domainId}
                  onChange={(e) => {
                    setDomainId(e.target.value);
                    setLabelIds([]);
                  }}
                >
                  <option value="">{t("choose")}</option>
                  {domains.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="flex flex-col gap-1">
                <legend>{t("format")}</legend>
                <span className="flex gap-3">
                  {(["COCO", "YOLO"] as const).map((f) => (
                    <label key={f} className="flex items-center gap-1">
                      <input type="radio" name="export-format" checked={format === f} onChange={() => setFormat(f)} />
                      {f}
                    </label>
                  ))}
                </span>
              </fieldset>
              <label className="flex flex-col gap-1">
                {t("since")}
                <input className={INPUT} type="date" value={since} onChange={(e) => setSince(e.target.value)} />
              </label>
            </div>
            {domainId && (labels.data?.length ?? 0) > 0 && (
              <fieldset className="flex flex-col gap-1">
                <legend>{t("labels")}</legend>
                <span className="flex flex-wrap gap-3">
                  {labels.data
                    ?.filter((l) => l.status === "ACTIVE")
                    .map((l) => (
                      <label key={l.id} className="flex items-center gap-1">
                        <input
                          type="checkbox"
                          checked={labelIds.includes(l.id)}
                          onChange={(e) => setLabelIds((ids) => (e.target.checked ? [...ids, l.id] : ids.filter((i) => i !== l.id)))}
                        />
                        {l.name}
                      </label>
                    ))}
                </span>
              </fieldset>
            )}
            {error && (
              <p role="alert" className="text-danger">
                {t(`errors.${error}`)}
              </p>
            )}
            <Button type="submit" busy={create.isPending} disabled={!domainId} className="self-start">
              {t("create")}
            </Button>
          </form>
        </Card>
      )}
      {exports.data && exports.data.exports.length > 0 && (
        <DataTable caption={t("exports")}>
          <THead>
            <Tr>
              <Th>{t("created")}</Th>
              <Th>{t("domain")}</Th>
              <Th>{t("format")}</Th>
              <Th>{t("photos")}</Th>
              <Th>{t("state")}</Th>
              <Th>
                <span className="sr-only">{t("download")}</span>
              </Th>
            </Tr>
          </THead>
          <TBody>
            {exports.data.exports.map((e) => (
              <Tr key={e.id}>
                <Td primary>
                  {formatter.dateTime(new Date(e.created_at), "dateTimeShort")}
                  <span className="block text-xs font-normal text-text-muted">{e.created_by_name}</span>
                </Td>
                <Td label={t("domain")}>{e.domain_name}</Td>
                <Td label={t("format")}>{e.format}</Td>
                <Td label={t("photos")}>
                  {t("count", { images: e.image_count, regions: e.region_count })}
                  {e.revoked_since > 0 && <span className="block text-xs text-warn">{t("revokedSince", { count: e.revoked_since })}</span>}
                </Td>
                <Td label={t("state")}>
                  <Badge tone={e.status === "READY" ? "ok" : e.status === "FAILED" ? "danger" : "neutral"}>{t(`status.${e.status}`)}</Badge>
                </Td>
                <Td>
                  {e.status === "READY" && tokens[e.id] ? (
                    <a
                      className="font-medium text-primary-strong underline"
                      href={downloadUrl(e.id, tokens[e.id])}
                      onClick={() => setTokens((current) => Object.fromEntries(Object.entries(current).filter(([id]) => id !== e.id)))}
                    >
                      {t("downloadOnce")}
                    </a>
                  ) : e.status === "READY" ? (
                    <span className="text-xs text-text-muted">{t("linkShown")}</span>
                  ) : null}
                </Td>
              </Tr>
            ))}
          </TBody>
        </DataTable>
      )}
    </section>
  );
}
