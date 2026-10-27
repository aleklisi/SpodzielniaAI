"use client";

import { useFormatter, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useToast } from "@/components/ui/Toast";
import { eta, llmErrorKey, useCancelGeneration, useIndex, useRebuildIndex, type IndexGeneration } from "@/lib/llm-admin";

const TONE: Record<IndexGeneration["status"], "primary" | "warn" | "neutral" | "danger"> = {
  SERVING: "primary",
  BUILDING: "warn",
  RETIRED: "neutral",
  CANCELLED: "danger",
};

function duration(seconds: number): string {
  if (seconds < 90) return `${seconds} s`;
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? `${minutes} min` : `${Math.round(minutes / 60)} h`;
}

function Generations({ title, rows, onCancel, busy }: { title: string; rows: IndexGeneration[]; onCancel: (n: number) => void; busy: boolean }) {
  const t = useTranslations("adminLlm.index");
  const format = useFormatter();
  const shown = rows.filter((g) => !g.dropped || g.status === "SERVING");
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-medium">{title}</h3>
      {shown.length === 0 && <p className="text-sm text-text-muted">{t("none")}</p>}
      <ul className="flex flex-col gap-2">
        {shown.map((g) => {
          const left = g.status === "BUILDING" ? eta(g) : null;
          const percent = g.total > 0 ? Math.min(100, Math.round((g.done / g.total) * 100)) : 100;
          return (
            <li key={g.number} className="rounded-lg border border-border p-3 text-sm" aria-label={t("versionN", { number: g.number })}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{t("versionN", { number: g.number })}</span>
                <Badge tone={TONE[g.status]}>{t(`statuses.${g.status}`)}</Badge>
                <span className="text-text-muted">{t("model", { model: g.model, dimension: g.dimension })}</span>
                {g.status === "BUILDING" && (
                  <Button variant="secondary" className="ml-auto" busy={busy} onClick={() => onCancel(g.number)}>
                    {t("cancel")}
                  </Button>
                )}
              </div>
              {g.status === "BUILDING" && (
                <div className="mt-2 flex flex-col gap-1">
                  <progress className="w-full" max={100} value={percent} aria-label={t("progress", { number: g.number })} />
                  <span className="text-xs text-text-muted">
                    {t("counts", { done: g.done, total: g.total, percent })}
                    {g.failed > 0 && t("errors", { count: g.failed })}
                    {left !== null && t("left", { time: duration(left) })}
                  </span>
                </div>
              )}
              {g.status === "RETIRED" && g.drop_after && !g.dropped && (
                <p className="mt-1 text-xs text-text-muted">{t("dropAfter", { date: format.dateTime(new Date(g.drop_after), "dateTime") })}</p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Index versions and the background re-embedding after a model change (10.3, 10.6). */
export function IndexPanel() {
  const t = useTranslations("adminLlm");
  const index = useIndex();
  const rebuild = useRebuildIndex();
  const cancel = useCancelGeneration();
  const toast = useToast();
  if (index.isPending) return <p className="text-text-muted">{t("loading")}</p>;
  if (!index.data) return <Card role="alert">{t("index.loadFailed")}</Card>;
  const data = index.data;

  async function run(work: () => Promise<unknown>, done: string) {
    try {
      await work();
      toast(done, "ok");
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="max-w-3xl text-sm text-text-muted">{t("index.intro", { perMinute: data.reembed_per_minute })}</p>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-lg font-semibold">{t("index.text")}</h2>
          <span className="text-sm text-text-muted">{t("index.textItems", { count: data.items })}</span>
          <Button
            variant="secondary"
            className="ml-auto"
            busy={rebuild.isPending}
            onClick={() => void run(() => rebuild.mutateAsync(), t("index.rebuilding"))}
          >
            {t("index.rebuild")}
          </Button>
        </div>
        <Generations
          title={t("index.versions")}
          rows={data.generations}
          busy={cancel.isPending}
          onCancel={(n) => void run(() => cancel.mutateAsync(n), t("index.cancelled"))}
        />
      </Card>
      <Card className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="font-display text-lg font-semibold">{t("index.atlas")}</h2>
          <span className="text-sm text-text-muted">{t("index.atlasItems", { count: data.atlas_items })}</span>
        </div>
        <Generations
          title={t("index.versions")}
          rows={data.atlas_generations}
          busy={cancel.isPending}
          onCancel={(n) => void run(() => cancel.mutateAsync(n), t("index.cancelled"))}
        />
      </Card>
    </div>
  );
}
