"use client";

import Link from "next/link";

import type { Attachment } from "@/lib/upload";
import { IMAGE_TYPES, imageUrl } from "@/lib/photos";

/** The note's photos with a way into the annotator („Oznacz”). */
export function PhotosPanel({ noteId, attachments }: { noteId: string; attachments: Attachment[] }) {
  const photos = attachments.filter((a) => IMAGE_TYPES.has(a.mime_type));
  if (photos.length === 0) return null;
  return (
    <section aria-labelledby="photos-title" className="flex flex-col gap-3">
      <h2 id="photos-title" className="font-display text-lg font-semibold">
        Zdjęcia
      </h2>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {photos.map((photo) => (
          <li key={photo.id} className="flex flex-col gap-1">
            <Link href={`/notebook/${noteId}/photos/${photo.id}`} className="group block overflow-hidden rounded-lg border border-border">
              {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes */}
              <img src={imageUrl(noteId, photo.id)} alt={photo.filename} loading="lazy" className="aspect-[4/3] w-full object-cover" />
            </Link>
            <Link href={`/notebook/${noteId}/photos/${photo.id}`} className="text-sm font-medium text-primary-strong">
              Oznacz: {photo.filename}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
