"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  ATLAS_STATUS,
  atlasImageUrl,
  CONSENT_LABEL,
  useMyAtlasPhotos,
  usePhotoConsent,
  usePhotoPreferences,
  type MyPhoto,
} from "@/lib/atlas-submissions";

const CONFIRM_WORD = "REVOKE";

/** „Zdjęcia w atlasie”: the author's consent and choices per photo (9.6). */
export function AtlasContributions() {
  const photos = useMyAtlasPhotos();
  const [revoking, setRevoking] = useState<MyPhoto | null>(null);
  if (!photos.data || photos.data.length === 0) return null;
  return (
    <section aria-labelledby="atlas-contributions-title" className="flex flex-col gap-3">
      <div>
        <h2 id="atlas-contributions-title" className="font-display text-xl font-semibold">
          Zdjęcia w atlasie
        </h2>
        <p className="text-sm text-text-muted">
          Wstrzymanie ukrywa zdjęcie w atlasie, w rozpoznawaniu i w eksportach; możesz je cofnąć. Trwałe wycofanie usuwa zdjęcie, opisy i
          oznaczenia. Eksportu, który już ktoś pobrał, nie da się cofnąć.
        </p>
      </div>
      <ul className="flex flex-col gap-2">
        {photos.data.map((photo) => (
          <li key={photo.id}>
            <PhotoRow photo={photo} onRevoke={() => setRevoking(photo)} />
          </li>
        ))}
      </ul>
      {revoking && <RevokePhotoDialog key={revoking.id} photo={revoking} onClose={() => setRevoking(null)} />}
    </section>
  );
}

function PhotoRow({ photo, onRevoke }: { photo: MyPhoto; onRevoke: () => void }) {
  const consent = usePhotoConsent();
  const preferences = usePhotoPreferences();
  const toast = useToast();
  const revoked = photo.consent_status === "REVOKED";
  const title = photo.labels.join(", ") || "Zdjęcie";

  async function run(action: "suspend" | "resume") {
    try {
      await consent.mutateAsync({ id: photo.id, action });
      toast(action === "suspend" ? "Zdjęcie wstrzymane." : "Zdjęcie znów udostępnione.", "ok");
    } catch {
      toast("Nie udało się zmienić zgody.", "error");
    }
  }

  async function prefer(patch: { show_author?: boolean; allow_export?: boolean }) {
    try {
      await preferences.mutateAsync({ id: photo.id, ...patch });
    } catch {
      toast("Nie udało się zapisać zmiany.", "error");
    }
  }

  return (
    <Card className="flex flex-wrap items-start gap-4">
      {revoked ? (
        <div aria-hidden className="h-20 w-28 rounded bg-surface-muted" />
      ) : (
        // eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes
        <img src={atlasImageUrl(photo.id, "thumb")} alt={title} className="h-20 w-28 rounded object-cover" />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
        <p className="font-medium">
          {revoked ? title : <Link href={`/atlas/${photo.id}`}>{title}</Link>}{" "}
          <Badge tone={CONSENT_LABEL[photo.consent_status].tone}>{CONSENT_LABEL[photo.consent_status].label}</Badge>{" "}
          {!revoked && <Badge tone={ATLAS_STATUS[photo.status].tone}>{ATLAS_STATUS[photo.status].label}</Badge>}
        </p>
        {photo.review_alert && !revoked && <p className="text-xs text-warn">{photo.review_alert}</p>}
        {!revoked && (
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={photo.show_author}
                disabled={preferences.isPending}
                onChange={(e) => void prefer({ show_author: e.target.checked })}
              />
              Pokazuj moje imię i nazwisko przy zdjęciu
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={photo.allow_export}
                disabled={preferences.isPending}
                onChange={(e) => void prefer({ allow_export: e.target.checked })}
              />
              Zgoda na użycie w eksporcie zbioru danych
            </label>
          </div>
        )}
      </div>
      {!revoked && (
        <div className="flex flex-wrap gap-2">
          {photo.consent_status === "ACTIVE" ? (
            <Button variant="secondary" busy={consent.isPending} onClick={() => void run("suspend")}>
              Wstrzymaj
            </Button>
          ) : (
            <Button variant="secondary" busy={consent.isPending} onClick={() => void run("resume")}>
              Wznów
            </Button>
          )}
          <Button variant="danger" onClick={onRevoke}>
            Wycofaj trwale
          </Button>
        </div>
      )}
    </Card>
  );
}

function RevokePhotoDialog({ photo, onClose }: { photo: MyPhoto; onClose: () => void }) {
  const [typed, setTyped] = useState("");
  const consent = usePhotoConsent();
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (typed !== CONFIRM_WORD) return;
    try {
      await consent.mutateAsync({ id: photo.id, action: "revoke" });
      toast("Zdjęcie wycofane z atlasu.", "ok");
      onClose();
    } catch {
      // Shown below (step-up or a generic error).
    }
  }

  return (
    <Dialog open onClose={onClose} title="Wycofaj zdjęcie trwale">
      {consent.error instanceof StepUpRequired ? (
        <StepUpNotice stepUp={consent.error.stepUp} reason="Trwałe wycofanie wymaga świeżego logowania." />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Wycofanie zdjęcia">
          <p>
            Zdjęcie „{photo.labels.join(", ")}” zniknie z atlasu razem z opisami i oznaczeniami. Tej operacji nie można cofnąć. Wpisz{" "}
            <strong>{CONFIRM_WORD}</strong>, aby potwierdzić.
          </p>
          <input
            className="rounded-lg border border-border bg-surface px-3 py-2"
            aria-label="Potwierdzenie"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          {consent.isError && !(consent.error instanceof StepUpRequired) && (
            <p role="alert" className="text-danger">
              Nie udało się wycofać zdjęcia.
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Anuluj
            </Button>
            <Button type="submit" variant="danger" busy={consent.isPending} disabled={typed !== CONFIRM_WORD}>
              Wycofaj trwale
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
