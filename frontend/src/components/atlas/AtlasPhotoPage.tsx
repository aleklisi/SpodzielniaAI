"use client";

import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";

import { ImageAnnotator, markColor } from "@/components/annotator/ImageAnnotator";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { Loading } from "@/components/ui/Skeleton";
import { ApiError, RateLimited, SecurityLocked } from "@/lib/api";
import { useAtlasPhoto } from "@/lib/atlas-gallery";
import { atlasImageUrl } from "@/lib/atlas-submissions";
import type { Mark } from "@/lib/photos";

export function AtlasPhotoPage({ imageId }: { imageId: string }) {
  const t = useTranslations("atlas.photo");
  const gallery = useTranslations("atlas.gallery");
  const format = useFormatter();
  const photo = useAtlasPhoto(imageId);
  const [selected, setSelected] = useState<string | null>(null);

  if (photo.isPending) return <Loading label={t("loading")} rows={1} />;
  if (photo.isError || !photo.data) {
    const error = photo.error;
    const message =
      error instanceof RateLimited
        ? t("rateLimited")
        : error instanceof SecurityLocked
          ? t("locked")
          : error instanceof ApiError && error.status === 404
            ? t("notFound")
            : t("loadFailed");
    return (
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold">{t("unavailable")}</h1>
        <p role="alert" className="text-text-muted">
          {message}
        </p>
        <Link href="/atlas" className="text-primary-strong underline">
          {t("back")}
        </Link>
      </div>
    );
  }

  const data = photo.data;
  const marks: Mark[] = data.regions.map((r) => ({ key: r.id, label: r.label, x: r.x, y: r.y, w: r.w, h: r.h, description: r.description ?? "", source: "USER" }));
  return (
    <div className="grid gap-6 @3xl:grid-cols-[minmax(0,1fr)_22rem]">
      <section aria-label={t("region")} className="flex min-w-0 flex-col gap-3">
        <Link href="/atlas" className="text-sm text-text-muted hover:text-text">
          {t("backShort")}
        </Link>
        <ImageAnnotator
          src={atlasImageUrl(data.id, "preview")}
          alt={data.caption ?? data.regions.map((r) => r.label.name).join(", ")}
          aspect={data.width / data.height}
          marks={marks}
          selected={selected}
          onSelect={setSelected}
          onChange={() => undefined}
          readOnly
        />
      </section>
      <aside className="flex flex-col gap-4">
        <Card className="flex flex-col gap-2 text-sm">
          <h1 className="font-display text-xl font-semibold">{data.regions.map((r) => r.label.name).filter((n, i, all) => all.indexOf(n) === i).join(", ")}</h1>
          <p className="text-text-muted">{data.domain_name}</p>
          {data.caption && <p className="whitespace-pre-wrap">{data.caption}</p>}
          <p className="text-xs text-text-muted">
            {t("author", { name: data.is_own ? t("you") : (data.author_name ?? gallery("member")) })}
            {data.verified_at && t("verified", { date: format.dateTime(new Date(data.verified_at), "long") })}
            {data.reviewers.length > 0 && t("experts", { names: data.reviewers.join(", ") })}
          </p>
          {data.is_own && data.status !== "APPROVED" && <p className="text-xs text-warn">{t("notVisible")}</p>}
        </Card>
        <section aria-labelledby="regions-title" className="flex flex-col gap-2">
          <h2 id="regions-title" className="font-display text-lg font-semibold">
            {t("marks")}
          </h2>
          <ol className="flex flex-col gap-2">
            {data.regions.map((r, index) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setSelected(r.id)}
                  aria-pressed={selected === r.id}
                  className={cn("w-full rounded-xl border bg-surface p-3 text-left text-sm", selected === r.id ? "border-primary" : "border-border")}
                >
                  <span className="flex items-center gap-2 font-medium">
                    <span aria-hidden className={cn("inline-block h-3 w-3 rounded-sm", markColor(index).chip)} />
                    {index + 1}. {r.label.name}
                  </span>
                  {r.description && <span className="mt-1 block text-text-muted">{r.description}</span>}
                </button>
              </li>
            ))}
          </ol>
        </section>
      </aside>
    </div>
  );
}
