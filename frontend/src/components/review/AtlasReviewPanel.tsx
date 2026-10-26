"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";

import { ImageAnnotator, markColor } from "@/components/annotator/ImageAnnotator";
import { LabelCombobox } from "@/components/atlas/LabelCombobox";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { PageHeader } from "@/components/ui/PageHeader";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { useDomains } from "@/lib/atlas";
import {
  reviewError,
  useAtlasDecision,
  useAtlasQueue,
  useCorrectRegions,
  useReviewPhoto,
  type ReviewDecision,
  type ReviewPhoto,
} from "@/lib/atlas-gallery";
import { atlasImageUrl, PRIVACY_LABEL } from "@/lib/atlas-submissions";
import type { Mark } from "@/lib/photos";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm";
const DECISIONS: { decision: ReviewDecision; label: string }[] = [
  { decision: "APPROVE", label: "Zatwierdź" },
  { decision: "REQUEST_CHANGES", label: "Poproś o poprawki" },
  { decision: "REJECT", label: "Odrzuć" },
];

/** The experts' queue of photos sent to the atlas. Authors are not shown. */
export function AtlasReviewPanel() {
  const queue = useAtlasQueue();
  const domains = useDomains();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useRef<HTMLElement>(null);
  const domainName = new Map((domains.data ?? []).map((d) => [d.id, d.name]));

  if (queue.error instanceof StepUpRequired) return <StepUpNotice stepUp={queue.error.stepUp} />;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Atlas: recenzje"
        description="Zdjęcia przekazane do atlasu. Autorzy nie są pokazywani."
        actions={
          <Link href="/review" className="text-sm font-medium text-primary-strong">
            Artykuły
          </Link>
        }
      />
      {/* Queue and photo side by side when there is room; one at a time on a phone (12.6). */}
      <div className="grid gap-6 @4xl:grid-cols-[20rem_minmax(0,1fr)]">
        <section aria-label="Kolejka" className={cn("flex flex-col gap-3", selected && "@max-4xl:hidden")}>
          {queue.isPending && <p className="text-text-muted">Ładowanie kolejki…</p>}
          {queue.data?.length === 0 && <p className="text-text-muted">Nie ma zdjęć do recenzji.</p>}
          <ul className="flex flex-col gap-2">
            {queue.data?.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelected(item.id);
                    detail.current?.scrollIntoView?.({ block: "start" });
                  }}
                  aria-current={item.id === selected}
                  className={cn("flex w-full gap-3 rounded-xl border bg-surface p-2 text-left", item.id === selected ? "border-primary" : "border-border")}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes */}
                  <img src={atlasImageUrl(item.id, "thumb")} alt="" className="h-16 w-20 rounded object-cover" />
                  <span className="flex flex-col gap-1 text-xs">
                    <span className="text-sm font-medium">{domainName.get(item.domain_id) ?? "Zdjęcie"}</span>
                    <span>
                      Oznaczenia: {item.regions} · zatwierdzenia {item.approvals}/{item.quorum}
                    </span>
                    {item.proposed_labels > 0 && <span className="text-warn">nowe etykiety: {item.proposed_labels}</span>}
                    {item.privacy_flags.length > 0 && <span className="text-warn">uwaga: prywatność</span>}
                    {item.is_own && <span className="text-text-muted">Twoje zdjęcie</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section ref={detail} aria-label="Recenzowane zdjęcie" className={cn("flex scroll-mt-20 flex-col gap-4", !selected && "@max-4xl:hidden")}>
          {selected && (
            <Button variant="ghost" className="self-start @4xl:hidden" onClick={() => setSelected(null)}>
              ← Kolejka
            </Button>
          )}
          {!selected && <Card className="text-text-muted">Wybierz zdjęcie z kolejki.</Card>}
          {selected && <ReviewedPhoto key={selected} id={selected} />}
        </section>
      </div>
    </div>
  );
}

function ReviewedPhoto({ id }: { id: string }) {
  const photo = useReviewPhoto(id);
  if (photo.isPending) return <p className="text-text-muted">Ładowanie…</p>;
  if (!photo.data) return <p role="alert" className="text-danger">Nie udało się wczytać zdjęcia.</p>;
  return <Reviewer key={photo.data.version} photo={photo.data} />;
}

function Reviewer({ photo }: { photo: ReviewPhoto }) {
  const correct = useCorrectRegions(photo.id);
  const decide = useAtlasDecision(photo.id);
  const toast = useToast();
  const [marks, setMarks] = useState<Mark[]>(() =>
    photo.regions.map((r) => ({ key: r.id, label: r.label, x: r.x, y: r.y, w: r.w, h: r.h, description: r.description ?? "", source: "USER" })),
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [decision, setDecision] = useState<ReviewDecision>("APPROVE");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const known = new Set(photo.regions.map((r) => r.id));
  const proposed = photo.regions.filter((r) => r.label.status === "PROPOSED");
  const readOnly = photo.is_own || photo.status !== "IN_REVIEW";

  function change(next: Mark[]) {
    setMarks(next);
    setDirty(true);
  }

  async function saveCorrections() {
    setError(null);
    try {
      await correct.mutateAsync({
        version: photo.version,
        regions: marks.map((m) => ({
          id: known.has(m.key) ? m.key : null,
          label_id: m.label!.id,
          x: m.x,
          y: m.y,
          w: m.w,
          h: m.h,
          description: m.description.trim() || null,
        })),
      });
      toast("Zapisano poprawki. Wcześniejsze zatwierdzenia trzeba powtórzyć.", "ok");
    } catch (e) {
      setError(reviewError(e));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await decide.mutateAsync({ decision, comment: comment.trim() || null, version: photo.version });
      toast(result.status === "APPROVED" ? "Zdjęcie trafiło do atlasu." : "Decyzja zapisana.", "ok");
    } catch (e) {
      setError(reviewError(e));
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
      <div className="flex min-w-0 flex-col gap-3">
        <ImageAnnotator
          src={atlasImageUrl(photo.id, "preview")}
          alt={photo.caption ?? "Zdjęcie do recenzji"}
          aspect={photo.width / photo.height}
          marks={marks}
          selected={selected}
          onSelect={setSelected}
          onChange={change}
          readOnly={readOnly}
        />
        <Card className="text-sm">
          <p className="text-text-muted">{photo.domain_name}</p>
          <p className="whitespace-pre-wrap">{photo.caption ?? "Bez opisu."}</p>
        </Card>
        {photo.history.length > 0 && (
          <Card className="text-sm">
            <h3 className="font-medium">Historia</h3>
            <ul className="mt-1 flex flex-col gap-1">
              {photo.history.map((h, i) => (
                <li key={i} className="text-text-muted">
                  {h.reviewer ?? "Ekspert"}: {DECISIONS.find((d) => d.decision === h.decision)?.label ?? h.decision} (wersja {h.image_version})
                  {h.comment && ` — ${h.comment}`}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
      <aside className="flex flex-col gap-3 text-sm">
        <p>
          <Badge tone="primary">
            Zatwierdzenia {photo.approvals}/{photo.quorum}
          </Badge>{" "}
          <span className="text-text-muted">wersja {photo.version}</span>
        </p>
        {photo.is_own && <p className="text-warn">To Twoje zdjęcie: recenzują je inni eksperci.</p>}
        {photo.privacy_check === "unavailable" && <p className="text-warn">Automatyczna kontrola prywatności była niedostępna.</p>}
        {photo.privacy_flags.length > 0 && (
          <p role="note" className="text-warn">
            Możliwe na zdjęciu: {photo.privacy_flags.map((f) => PRIVACY_LABEL[f] ?? f).join(", ")}. Autor potwierdził zgodę lub
            nierozpoznawalność.
          </p>
        )}
        {proposed.length > 0 && (
          <p className="text-warn">
            Nowe etykiety ({proposed.map((r) => r.label.name).join(", ")}) trzeba najpierw rozpatrzyć w{" "}
            <Link className="underline" href="/review/labels">
              słowniku etykiet
            </Link>
            .
          </p>
        )}
        <ol className="flex flex-col gap-2">
          {marks.map((mark, index) => {
            const region = photo.regions.find((r) => r.id === mark.key);
            return (
              <li key={mark.key}>
                <Card className={cn("flex flex-col gap-2 p-3", mark.key === selected && "border-primary")} onClick={() => setSelected(mark.key)}>
                  <span className="flex items-center gap-2 font-medium">
                    <span aria-hidden className={cn("inline-block h-3 w-3 rounded-sm", markColor(index).chip)} />
                    Oznaczenie {index + 1}
                    {region?.corrected && <span className="text-xs text-text-muted">(poprawione)</span>}
                    {region?.by_reviewer && <span className="text-xs text-text-muted">(dodane przez eksperta)</span>}
                    {!region && <span className="text-xs text-text-muted">(nowe)</span>}
                  </span>
                  <LabelCombobox
                    domainId={photo.domain_id}
                    label={`Etykieta oznaczenia ${index + 1}`}
                    value={mark.label}
                    disabled={readOnly}
                    onChange={(l) => change(marks.map((m) => (m.key === mark.key ? { ...m, label: { id: l.id, name: l.name, status: l.status } } : m)))}
                  />
                  <textarea
                    className={INPUT}
                    rows={2}
                    aria-label={`Opis oznaczenia ${index + 1}`}
                    disabled={readOnly}
                    value={mark.description}
                    onChange={(e) => change(marks.map((m) => (m.key === mark.key ? { ...m, description: e.target.value } : m)))}
                  />
                  {!readOnly && (
                    <Button variant="ghost" className="self-end px-2 py-1" onClick={() => change(marks.filter((m) => m.key !== mark.key))}>
                      Usuń
                    </Button>
                  )}
                </Card>
              </li>
            );
          })}
        </ol>
        {!readOnly && (
          <Button variant="secondary" onClick={() => void saveCorrections()} busy={correct.isPending} disabled={!dirty || marks.some((m) => !m.label)}>
            Zapisz poprawki
          </Button>
        )}
        {!readOnly && (
          <form onSubmit={submit} className="flex flex-col gap-2" aria-label="Decyzja">
            <fieldset className="flex flex-col gap-1">
              <legend className="font-medium">Decyzja</legend>
              {DECISIONS.map((d) => (
                <label key={d.decision} className="flex items-center gap-2">
                  <input type="radio" name="atlas-decision" checked={decision === d.decision} onChange={() => setDecision(d.decision)} />
                  {d.label}
                </label>
              ))}
            </fieldset>
            <label className="flex flex-col gap-1">
              Komentarz dla autora {decision !== "APPROVE" && "(wymagany)"}
              <textarea className={INPUT} rows={3} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
            </label>
            {dirty && <p className="text-xs text-warn">Najpierw zapisz poprawki.</p>}
            <Button type="submit" busy={decide.isPending} disabled={dirty || (decision !== "APPROVE" && !comment.trim())}>
              Zapisz decyzję
            </Button>
          </form>
        )}
        {error && (
          <p role="alert" className="text-danger">
            {error}
          </p>
        )}
      </aside>
    </div>
  );
}
