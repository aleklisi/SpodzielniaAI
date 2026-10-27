"use client";

import { NotebookPen, Plus, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { RateLimited } from "@/lib/api";
import { isModelMissing, useFeature } from "@/lib/capabilities";
import { useCreateNote, useNotes, usePromote, usePromotion } from "@/lib/notebook";

export function NotebookList() {
  const t = useTranslations("notebook.list");
  const format = useFormatter();
  const notes = useNotes();
  const create = useCreateNote();
  const router = useRouter();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promotionId, setPromotionId] = useState<string | null>(null);

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <Button variant="secondary" disabled={selected.size === 0} onClick={() => setPromoteOpen(true)}>
              <Send aria-hidden className="size-4" />
              {t("promote", { count: selected.size })}
            </Button>
            <Button
              busy={create.isPending}
              onClick={async () => {
                const note = await create.mutateAsync(t("newNote"));
                router.push(`/notebook/${note.id}`);
              }}
            >
              <Plus aria-hidden className="size-4" />
              {t("newNote")}
            </Button>
          </>
        }
      />

      {promotionId && <PromotionStatus promotionId={promotionId} onDismiss={() => setPromotionId(null)} />}

      {notes.isPending && <Loading />}
      {notes.isError && <p role="alert" className="text-danger">{t("loadFailed")}</p>}
      {notes.data?.length === 0 && (
        <EmptyState icon={NotebookPen} title={t("emptyTitle")}>
          {t("emptyBody")}
        </EmptyState>
      )}
      {notes.data && notes.data.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-label={t("notes")}>
          {notes.data.map((note) => (
            <li key={note.id}>
              <Card
                className={cn(
                  "relative flex h-full items-start gap-3 p-4 transition-colors hover:border-primary",
                  selected.has(note.id) && "border-primary bg-primary/5",
                )}
              >
                <input
                  type="checkbox"
                  checked={selected.has(note.id)}
                  onChange={() => toggle(note.id)}
                  aria-label={t("select", { title: note.title })}
                  className="relative z-10 mt-1 size-4 accent-primary pointer-coarse:size-5"
                />
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  {/* The link covers the card; the checkbox sits above it. */}
                  <Link
                    href={`/notebook/${note.id}`}
                    className="font-medium break-words after:absolute after:inset-0 after:rounded-xl hover:text-primary-strong"
                  >
                    {note.title}
                  </Link>
                  <time dateTime={note.updated_at} className="text-xs text-text-muted">
                    {format.dateTime(new Date(note.updated_at), "dateTime")}
                  </time>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <PromoteDialog
        open={promoteOpen}
        noteIds={[...selected]}
        onClose={() => setPromoteOpen(false)}
        onPromoted={(id) => {
          setPromoteOpen(false);
          setSelected(new Set());
          setPromotionId(id);
        }}
      />
    </div>
  );
}

function PromoteDialog({
  open,
  noteIds,
  onClose,
  onPromoted,
}: {
  open: boolean;
  noteIds: string[];
  onClose: () => void;
  onPromoted: (promotionId: string) => void;
}) {
  const t = useTranslations("notebook.promote");
  const common = useTranslations("common");
  const [titleHint, setTitleHint] = useState("");
  const promote = usePromote();
  const available = useFeature("promotion");
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const result = await promote.mutateAsync({ noteIds, titleHint });
      toast(t("accepted"), "ok");
      onPromoted(result.promotion_id);
    } catch (error) {
      toast(
        error instanceof RateLimited
          ? t("rateLimited")
          : isModelMissing(error)
            ? t("modelMissing")
            : t("failed"),
        "error",
      );
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={t("title")}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
        <p>{t("body", { count: noteIds.length })}</p>
        {!available && <ModelMissing what={t("feature")} />}
        <label className="flex flex-col gap-1">
          {t("titleHint")}
          <input
            value={titleHint}
            maxLength={200}
            onChange={(event) => setTitleHint(event.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2"
          />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" busy={promote.isPending} disabled={!available}>
            {t("submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function PromotionStatus({ promotionId, onDismiss }: { promotionId: string; onDismiss: () => void }) {
  const t = useTranslations("notebook.promote");
  const common = useTranslations("common");
  const promotion = usePromotion(promotionId);
  const data = promotion.data;
  return (
    <Card role="status" className="flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm">
        <p className="font-medium">{data ? t(`status.${data.status}`) : t("status.checking")}</p>
        {data?.status === "DONE" && (
          <p className="text-text-muted">
            {t("piiRemoved", { count: Object.values(data.pii_stats as Record<string, number>).reduce((a, b) => a + b, 0) })}{" "}
            <Link href={`/wiki/${data.article_id}`} className="text-primary-strong underline">
              {t("viewDraft")}
            </Link>
          </p>
        )}
        {data?.status === "FAILED" && <p className="text-danger">{t("retryLater")}</p>}
      </div>
      <Button variant="ghost" onClick={onDismiss}>
        {common("close")}
      </Button>
    </Card>
  );
}
