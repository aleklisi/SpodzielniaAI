# Stage 9 — Visual Atlas (annotated photos, expert-verified dataset, image recognition)

**User story:** [US-03](../USER_STORIES_LOG.md#us-03) — *As a member I want to add a photo with a
description, mark one or more elements on it and describe them, so that the cooperative builds
verified knowledge for image recognition (e.g. a beekeeper adds photos of queen bees).*

The atlas is **multi-domain**: beekeeping is only the example use case. Every domain of the
cooperative (beekeeping, gardening, crafts, equipment…) gets its own label vocabulary in the
same atlas.
**Spec refs:** `AGENTS.md` §3 (RLS, block-level provenance, anti-distillation, local LLM for
private data) · `docs/ARCHITECTURE.md` §2 (Push-to-Wiki, Expert Council), §3 (consent, exit
strategy), §4 (VLM in the ingestion pipeline) · `docs/SECURITY.md` (shield layers 1, 3, 5) ·
chunks 2.3 (EXIF), 2.5 (media workers), 3.2 (PII), 3.3 (injection), 3.5 (promotion), 4.2
(governance, quorum), 4.3 (consent), 4.4 (outbox), 5.2 (coverage quota), 7.x (VLM purpose).

> This stage is **outside the original 6-stage plan**. It starts only on an explicit instruction.
> The Definition of Done from `00_OVERVIEW.md` §2.1 applies to every chunk (ruff, pyright strict,
> pytest; npm lint/typecheck/test for frontend chunks).

**Refinement decisions (product owner, 2026-10-21):**
- **Purpose: both, phased.** Phase A builds the annotated, expert-verified dataset (9.1–9.7);
  Phase B adds in-app recognition on top of it (9.8–9.9). Phase A is useful on its own.
- **Visibility: private, then contribute.** Photos are annotated in the private notebook; the
  member sends a photo to the shared atlas, the Expert Council verifies it, and the author keeps
  suspend/revoke rights, exactly like wiki blocks.
- **Labels: expert-curated vocabulary + free text.** Every marked element has a label from a
  shared vocabulary (members may propose new labels) and its own description.
- **Shapes: rectangles only** (bounding boxes). Polygons and points are follow-ups.
- **Multi-domain (2026-10-21, answer to open question 1):** labels are grouped into domains
  managed in the app; nothing is beekeeping-specific in code or production seed data.
- **Author visibility is the author's choice (answer to open question 2):** per photo, set
  when sending and changeable at any time.
- **Dataset export needs a separate opt-in (answer to open question 3):** `allow_export` per
  photo, off unless the author ticks it.

UI name: **„Atlas”** (nav item), photos in the atlas: *„zdjęcia atlasu”*, a marked element:
*„oznaczenie”*, the vocabulary: *„słownik etykiet”*.

---

## 0. Current state (what we build on)

- **Photos already enter the private notebook** as `NoteAttachment` (`image/jpeg|png|webp|heic`,
  2.2). The upload pipeline scans them (ClamAV), applies the EXIF orientation and re-encodes the
  pixels, dropping all metadata except GPS when the member ticked `keep_location` (2.3). The
  object in MinIO is client-side encrypted under the attachment's DEK.
- **The local VLM describes images** (`workers/engines/vision.py`, purpose `VLM`, always
  `force_local_llm=True`) into an encrypted `IMAGE_DESCRIPTION` derivative, which is then
  scanned for prompt injection (3.3) and indexed into `private_notes_index`.
- **Nothing serves image bytes back to the browser today.** The API returns only metadata and
  derived text. The frontend CSP already allows `img-src 'self' blob: data:`.
- **The shared zone is text only.** Push-to-Wiki (3.5) turns notes into article text; images never
  leave the private zone. `ArticleBlock` carries `author_id` + `consent_status`, the Qdrant
  points mirror them through the vector outbox (4.4), and `ConsentService` (4.3) suspends,
  resumes and revokes per author.
- **Expert review** has a queue, decisions with comments, a two-expert quorum and optimistic
  locking on `WikiArticle.version` (4.2).
- **Embeddings are text only** (`llm.embed_model`, one vector size per Qdrant collection).
- **The shield** (5.x) counts unique canonical blocks per user (coverage quota), and can degrade
  or lock an account.

## 1. Goals and non-goals

**Goals — Phase A (dataset)**
1. **Annotate a photo privately:** in a note, open a photo, write a caption, draw rectangles on
   elements, give each a label from the vocabulary and a description. Works with mouse, touch
   and keyboard.
2. **Domains and label vocabulary:** admins create domains (e.g. „Pszczelarstwo”,
   „Ogrodnictwo”); experts curate each domain's labels (name, description, synonyms); members
   pick from it and may propose a missing label, which an expert approves, merges or rejects.
3. **Contribute to the atlas:** "Przekaż do atlasu" sends one annotated photo for review. A
   shared copy is made with all metadata (including GPS) removed, texts are PII-scrubbed and
   scanned for injection, and the photo is checked for people and documents.
4. **Expert verification:** experts see the photo with the boxes, can correct boxes and labels,
   add missing marks, and approve, reject or request changes (two-expert quorum as for articles).
5. **Shared atlas:** members browse approved photos by label, with boxes and descriptions.
6. **Consent:** the author suspends, resumes or revokes each atlas photo from "Mój wkład";
   revoke deletes the shared image, its regions and vectors.
7. **Dataset export:** an admin exports approved, consented photos as COCO JSON or YOLO with a
   manifest (ADMIN + MFA + step-up, disabled by default by the operator).

**Goals — Phase B (recognition)**
8. **Visual index:** image embeddings of approved photos and of each marked region (local model,
   offline), in a new Qdrant collection with provenance payload.
9. **„Rozpoznaj”:** a member asks about a photo in their notebook ("Czy na zdjęciu jest matka?").
   The system finds similar verified regions, the local VLM answers with suggested boxes and
   cites the atlas photos it relied on. Suggested boxes can be accepted as the member's own
   annotations (this speeds up annotating and closes the loop).

**Non-goals (v1)**
- Polygons, points, segmentation masks, video frames.
- Training or fine-tuning a detector inside the app (the export feeds external training).
- Face/plate blurring tools (v1 flags such photos for the member and the experts instead).
- Annotating photos directly in the shared atlas without the private step.
- Cloud vision models for any of this: every step is local (VLM purpose is local-only, 7.1).
- Domain-scoped experts (any EXPERT reviews any domain in v1; follow-up if the council grows).

## 2. Security and governance invariants (must hold after this stage)

| # | Invariant | Enforcement |
|---|-----------|-------------|
| A1 | Private photos and annotations stay private. | New private tables (`annotated_image`, `image_annotation`, `atlas_submission`) have `owner_id` and RLS through `app.current_user_id`, like `PrivateNote`. Captions and descriptions are AES-GCM ciphertext under the note's DEK scheme. Integration test: user B gets 404 for user A's photo, annotations and image bytes, even with a direct id. |
| A2 | Image bytes are served safely. | `GET …/image` only for image attachments the caller owns (RLS) or for atlas photos the caller may see; served from the sanitized object with the stored MIME type, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: private, no-store`. No SVG, ever (not an accepted type). |
| A3 | Nothing identifying leaves with a contributed photo. | The shared copy is re-encoded from pixels only; GPS is dropped regardless of `keep_location`; the original filename is not copied. Caption and region descriptions go through Presidio scrubbing with `force_local_llm=True` and the injection guard. The local VLM flags people, faces, documents, number plates and screens (`privacy_flags`), shown to the member before sending and to the experts in review. |
| A4 | Block-level provenance covers the atlas. | `atlas_image` and every `atlas_region` carry `author_id` + `consent_status`; every Qdrant point in `atlas_visual_index` carries `author_id`, `consent_status`, `is_active`, `atlas_image_id`, `region_id`, `label_id`. Same CHECK as blocks: a REVOKED row is a tombstone (no object key, no text). |
| A5 | Only verified content is shared or indexed. | Members see and recognition uses only `APPROVED` atlas photos with `consent_status = ACTIVE`; the vector filter is mandatory in the repository (as in 5.1). Rejected or pending submissions are visible to the author and the reviewers only. |
| A6 | Revoke really removes. | Revoke deletes the shared object in MinIO, wipes captions and descriptions, deletes the points (outbox), and is final. Export manifests that included the photo are listed to admins as "zawiera wycofane zdjęcia" (an export already handed out cannot be recalled; see §5). |
| A7 | The atlas can't be scraped. | Atlas views and recognition citations count toward the shield coverage quota as a new dimension (`atlas_images`); list endpoints return thumbnails only, max 48 per page; full-size images are rate-limited per user. The API never returns embeddings or raw vector hits: recognition returns only the VLM answer, suggested boxes and citations. |
| A8 | Recognition never leaves the server. | The query photo is a private attachment; embedding and VLM run locally (`force_local_llm=True`); the answer is stored as a private derivative. |
| A9 | The dataset export is deliberate and attributable. | Off unless the operator sets `atlas.export_enabled=true` (env, the UI cannot widen it). ADMIN + MFA + step-up (≤ 5 min), max 5 exports per day, built by a worker into an encrypted archive with a single-use download link valid 24 h, `ATLAS_EXPORTED` audit event with the manifest hash and counts. |
| A10 | Labels are governed. | Only EXPERT/ADMIN create, edit, merge or deprecate labels; members only propose. A deprecated label stays on existing regions (shown greyed) and can't be chosen for new ones; a merge re-points regions and updates point payloads through the outbox. |
| A11 | Uploaded images can't attack the workers. | Existing 2.2/2.3 rules (magic bytes, ClamAV, Pillow decompression-bomb limit, re-encode) also apply to the shared copy and the embedding crops; region coordinates are validated server-side (normalised `0 ≤ x, y` and `x + w ≤ 1`, `y + h ≤ 1`, min size 0.5 % of a side, max 50 regions per photo). |
| A12 | The author's attribution choice is respected everywhere. | With `show_author=false` the author's name and id never appear in member-facing responses (atlas list and page, recognition citations, label proposals linked to the photo); reviewers' queue already hides identity; only the author, admins (audit) and the database keep `author_id`. Export manifests hash author ids per export. Test: an anonymous photo's API responses contain no author name or id for another member. |

## 3. Design

### 3.1 Domains and label vocabulary (migration `0014_visual_labels`, shared zone)

```
visual_domain id, slug (unique), name (pl), description, status ACTIVE|ARCHIVED,
              created_by, created_at
visual_label  id, domain_id → visual_domain, slug (unique per domain), name (pl),
              description, synonyms text[], status PROPOSED|ACTIVE|DEPRECATED,
              merged_into_id → visual_label (same domain), proposed_by, approved_by,
              created_at, updated_at
```

- **No domain-specific production seed.** Migrations create empty tables; admins create
  domains, experts fill them. The same label name may exist in two domains ("larwa" in
  beekeeping and in gardening pests) without clashing.
- An **example domain** for dev, demos and tests is loaded by `spoldzielnia atlas
  seed-example` (not run in prod): „Pszczelarstwo” with *matka pszczela, robotnica, truteń,
  jajo, czerw otwarty, czerw kryty, matecznik, miseczka mateczna, pierzga, miód kryty, warroza
  (Varroa destructor), zgnilec (objawy), plaster*. E2E and the 9.8 benchmark use it.
- Domains: ADMIN + MFA creates, renames and archives them (an archived domain keeps its photos
  and labels read-only, and accepts no new photos). Audit `DOMAIN_CREATED`, `DOMAIN_CHANGED`.
- `wiki/labels.py` → `LabelService`: `search` (name + synonyms, accent-insensitive via
  `unaccent`), `propose` (member, max 10 per day), `approve`, `edit`, `merge`, `deprecate`.
- API `/domains` (`GET` members; `POST`/`PATCH` ADMIN + MFA) and `/labels?domain=`: `GET`
  (members, ACTIVE + own PROPOSED), `POST /labels/proposals` (member),
  `POST /labels`, `PATCH /labels/{id}`, `POST /labels/{id}/merge`, `POST /labels/{id}/deprecate`
  (EXPERT/ADMIN + MFA). Audit `LABEL_PROPOSED`, `LABEL_APPROVED`, `LABEL_CHANGED`,
  `LABEL_MERGED`, `LABEL_DEPRECATED`.
- Frontend: label combobox scoped to the photo's domain (search, synonyms, "Zaproponuj nową
  etykietę"); experts get a „Słownik etykiet” tab in `/review` with a domain selector; admins
  manage domains in `/admin` („Dziedziny atlasu”).

### 3.2 Private photo annotations (migration `0015_image_annotation`, private zone)

```
annotated_image   id, owner_id (RLS), note_id, attachment_id (unique, image only),
                  domain_id → visual_domain (chosen first; labels come from it),
                  width, height (of the sanitized, orientation-corrected image),
                  caption_ciphertext, dek_wrapped, version, updated_at
image_annotation  id, owner_id (RLS), annotated_image_id, label_id → visual_label,
                  x, y, w, h  (double precision, normalised 0..1, CHECKs per A11),
                  description_ciphertext, source USER|SUGGESTED, position
```

- Coordinates are normalised to the **stored** image (already rotated by `exif_transpose` in
  2.3), so they survive any display size and the shared copy.
- `notebook/annotations.py` → `AnnotationService` (RLS session): `get(attachment)`,
  `save(attachment, caption, regions, expected_version)` replaces the full set in one
  transaction with optimistic locking (simple, matches how the editor saves).
- API (owner only, under the existing notes router):
  - `GET /notes/{note_id}/attachments/{id}/image?size=preview|full` — streamed and decrypted;
    `preview` is a 1600 px rendition produced once by the upload worker and stored encrypted
    as a second object (`preview_object_key` on `note_attachment`), so phones never download
    20 MB originals. Headers per A2.
  - `GET /notes/{note_id}/attachments/{id}/annotations` → caption + regions + version.
  - `PUT /notes/{note_id}/attachments/{id}/annotations` → full replace, 409 `stale_version`.
- The annotations also feed the private RAG: on save, a short text derivative
  ("Zdjęcie: <caption>. Oznaczenia: matka pszczela – <opis> …") replaces the previous one and is
  indexed into `private_notes_index`, so "where did I photograph the queen?" works in the
  member's own notebook chat.

### 3.3 Annotator component (frontend)

- `components/annotator/ImageAnnotator.tsx`: the image in a responsive box with an SVG overlay
  (no canvas, so boxes stay crisp and are DOM-accessible). Draw by dragging (pointer events,
  touch included), move by dragging the box, resize by 8 handles, delete with a button or
  `Delete`. Keyboard: `Tab` cycles boxes, arrows move 1 % (Shift: 0.2 %), `Alt`+arrows resize.
  Zoom 1–4× with pan for small elements (eggs, mites).
- Side list (below the image on mobile): one row per box with colour, label combobox and
  description; selecting a row highlights the box and vice versa. Caption field above.
- Unsaved-changes guard, "Zapisz" with optimistic locking, `stale_version` → reload prompt.
- Entry point: a „Oznacz” button on image attachments in `NoteWorkspace`, opening
  `/notebook/[noteId]/photos/[attachmentId]`.

### 3.4 Contribution to the atlas (migration `0016_atlas`)

```
atlas_submission (private, RLS)  id, owner_id, annotated_image_id, atlas_image_id,
                                 status PENDING|PREPARED|SENT|FAILED, error, created_at
atlas_image  (shared)  id, author_id, consent_status, status DRAFT|IN_REVIEW|APPROVED|
                       REVISION_REQUIRED|REJECTED, object_key, preview_object_key,
                       thumb_object_key, width, height, sha256, phash, caption,
                       privacy_flags jsonb (kinds + scores only), domain_id,
                       allow_export bool, show_author bool, version,
                       verified_at, review_alert, created_at
atlas_region (shared)  id, atlas_image_id, author_id, consent_status, label_id,
                       x, y, w, h, description, corrected_by, position
atlas_review (shared)  id, atlas_image_id, reviewer_id, decision, comment
```

- **Flow** (`POST /atlas/submissions {attachment_id, confirm_no_people: bool, allow_export:
  bool, show_author: bool}`, max 20 per day): a worker job (`worker-core`, no network) decrypts the private image, re-encodes a shared
  copy (pixels only, max 4096 px, JPEG q90, preview 1600 px, thumbnail 320 px), computes
  SHA-256 and a perceptual hash (duplicate check against existing atlas photos → "to zdjęcie
  już jest w atlasie"), scrubs caption and descriptions (Presidio, `force_local_llm=True`),
  runs the injection guard on them, and asks the local VLM for `privacy_flags`. Result: an
  `atlas_image` in `IN_REVIEW` with its regions; the member sees the scrubbed texts and flags
  first and confirms (`SENT`) or withdraws.
- The dialog asks two explicit questions with nothing preselected: „Pokaż moje imię i nazwisko
  przy zdjęciu” / „Publikuj anonimowo” (`show_author`), and „Zgadzam się na użycie zdjęcia w
  eksporcie zbioru danych” (`allow_export`, unchecked by default).
- A photo with a `person`/`face` flag can be sent only if the member ticked
  `confirm_no_people` ("osoby na zdjęciu wyraziły zgodę lub są nierozpoznawalne"); experts see
  the flag regardless.
- Shared objects are encrypted with a per-image DEK wrapped by the KEK (same crypto as private
  objects; the shared zone is protected at rest too).
- Re-submitting after `REVISION_REQUIRED` updates the same `atlas_image` (new version), keeping
  the review history.

### 3.5 Expert review and the shared atlas

- Review queue gets an „Atlas” tab: photo with boxes, scrubbed caption, privacy flags, label
  proposals used by the photo, author's history count (not identity in the queue list, as for
  articles).
- An expert may adjust boxes, change a region's label, add regions (authored by the expert),
  and delete regions; `corrected_by` records who changed a member's region. Decisions:
  `APPROVE`, `REQUEST_CHANGES` (comment required), `REJECT` (reason code). Approval needs two
  experts, like articles; the second approver sees the first one's corrections as a diff
  (boxes before/after overlaid).
- A photo that uses a PROPOSED label can't be approved until that label is approved or merged.
- `/atlas` (members): grid of approved photos (thumbnails), filters by domain and label,
  search by label/synonym; photo page with the image, boxes, caption, region descriptions,
  the author's display name **only if `show_author`** (otherwise „Członek spółdzielni”),
  verification date and reviewers. The review queue filters by domain.
- `ATLAS_SUBMITTED`, `ATLAS_REVIEWED`, `ATLAS_STATUS_CHANGED` audit events; metrics
  `atlas_submissions_total{outcome}`, `atlas_review_seconds`.

### 3.6 Consent for atlas photos

- `ConsentService` is generalised to "contribution kinds": `article_block` and `atlas_image`.
  Suspend/resume/revoke act on the whole photo (the image is one author's work). Regions added
  by an expert to a member's photo follow the photo.
- Suspend: hidden from `/atlas`, points `is_active=false`. Resume: back only if still
  `APPROVED`. Revoke: objects deleted from MinIO, caption/description wiped (tombstone, CHECK),
  points deleted, exports that contained it marked.
- „Mój wkład” lists atlas photos next to articles, with thumbnails and two switches per
  photo: `show_author` (name visible or anonymous, takes effect immediately) and `allow_export`
  (consent to dataset export, separate from consent to the atlas; turning it off excludes the
  photo from future exports). Both changes are audited (`ATLAS_ATTRIBUTION_CHANGED`,
  `ATLAS_EXPORT_CONSENT_CHANGED`).

### 3.7 Dataset export

- `POST /admin/atlas/exports {format: coco|yolo, domain, labels?: [..], min_verified_at?}`
  (A9, one domain per export, so class lists stay coherent) →
  job in `worker-core`: selects `APPROVED` + `ACTIVE` + `allow_export`, writes images (shared
  copies, renamed to opaque ids), annotations in COCO JSON or YOLO txt + `classes.txt`, a `manifest.json`
  (image ids, sha256, label ids, author ids hashed with a per-export salt, export id, created
  by, licence note "do użytku wewnętrznego spółdzielni"), zipped and encrypted in MinIO.
- `GET /admin/atlas/exports` lists exports with counts and the "contains revoked photos" flag;
  `GET /admin/atlas/exports/{id}/download?token=` streams it once.
- Settings `AtlasSettings`: `export_enabled=false`, `exports_per_day=5`,
  `export_link_ttl_seconds=86400`.

### 3.8 Visual index (Phase B)

- **Model:** an image-embedding model run locally in `worker-media` (CPU is enough), pinned like
  Whisper (`spoldzielnia models` downloads a pinned revision, SHA-256 in code, manifest
  re-verified). Default candidate: SigLIP base patch16-224 (multilingual-friendly, good on
  fine-grained crops); the choice is confirmed by a small benchmark on the seed atlas in 9.8.
  Settings `models.image_embed_model`, `qdrant.image_vector_size`.
- **Collection `atlas_visual_index`:** one point per approved photo (whole image) and one per
  region (crop with 10 % margin, min 64 px). Payload (ids and governance only, overview §2.5):
  `atlas_image_id`, `region_id|null`, `label_id`, `author_id`, `consent_status`, `is_active`,
  `kind image|region`, `verified_at`. Payload indexes on all filter fields.
- Points are written only through the vector outbox (new op target `ATLAS`), so approve,
  suspend, revoke and label merge stay consistent with Postgres; the 4.4 consistency check and
  `reindex` CLI cover the new collection.

### 3.9 Recognition „Rozpoznaj” (Phase B)

- `POST /notes/{note_id}/attachments/{id}/recognize {question?}` (member, owner, 30 per day) →
  job in `worker-media`: embed the whole photo and a grid of overlapping crops (3×3 + 2×2),
  search `atlas_visual_index` with the mandatory filter (A5) and the photo's domain (the
  member may widen it to all domains), aggregate hits per label (score
  threshold from settings, calibrated in 9.8). No hit above the threshold → "W atlasie nie ma
  zweryfikowanych przykładów podobnych do tego zdjęcia" (strict grounding, no VLM guessing).
- Otherwise the local VLM gets the member's photo, the candidate labels with their vocabulary
  descriptions and up to 3 reference crops, and returns JSON: answer text, per-label boxes
  (normalised) with confidence. The answer passes the injection/verbatim guards like RAG
  answers.
- Stored as a private `RECOGNITION` derivative (encrypted): answer, suggested boxes, cited
  `atlas_image_id`s. UI: suggestions drawn dashed on the photo with „Przyjmij” / „Odrzuć”;
  accepted ones become `image_annotation` rows with `source = SUGGESTED`.
- Citations show atlas thumbnails and link to `/atlas/{id}`; each cited photo counts toward the
  member's coverage quota (A7). Metrics `atlas_recognitions_total{outcome}`,
  `atlas_recognition_seconds`.

## 4. Chunks

### Phase A — annotated, expert-verified dataset

### Chunk 9.1 — Label vocabulary
- Migration `0014_visual_labels` (domains + labels, no seed), `seed-example` CLI,
  `LabelService`, `/domains` and `/labels` API, audit events, label combobox, the experts'
  „Słownik etykiet” tab and the admins' „Dziedziny atlasu” panel.
- **Tests:** unit — slug/synonym search with Polish diacritics, proposal daily limit, merge
  re-points only within a domain; integration — authz matrix (member proposes only; EXPERT
  without MFA → 403; only ADMIN manages domains), same label name in two domains, archived
  domain refuses new photos, deprecated label not selectable, audit rows; Vitest — combobox search, propose flow.

### Chunk 9.2 — Private photo annotations and image serving
- Migration `0015_image_annotation` (+ `preview_object_key` on `note_attachment`), preview
  rendition in the upload worker (and a backfill CLI for existing photos), `AnnotationService`,
  image + annotations endpoints, annotation text derivative indexed privately.
- **Tests:** RLS (A1) for tables and image bytes; headers (A2); coordinate validation and
  limits (A11); optimistic locking 409; non-image attachment → 404; derivative re-indexed on save
  and removed with the note; preview never larger than 1600 px and has no metadata.

### Chunk 9.3 — Annotator UI in the notebook
- `ImageAnnotator`, photo page in the notebook, „Oznacz” entry point, unsaved-changes guard.
- **Tests:** Vitest + MSW — draw/move/resize/delete with pointer events, keyboard operation,
  label + description per region, save with stale version prompts reload, mobile layout snapshot;
  axe check on the page.

### Chunk 9.4 — Contribute to the atlas
- Migration `0016_atlas`, `AtlasSubmissionService`, worker job (shared copy, hashes, duplicate
  check, PII scrub, injection scan, VLM privacy flags), `/atlas/submissions` API, „Przekaż do
  atlasu” dialog with the scrubbed preview and flags.
- **Tests:** shared copy has no EXIF/GPS even with `keep_location` (A3); PII in caption is
  scrubbed (PESEL, phone, name); injection text in a description is flagged and blocks sending;
  duplicate photo detected; person flag requires `confirm_no_people`; VLM unavailable → photo
  goes to review with `privacy_check: unavailable` and a visible warning (not silently clean);
  provenance columns set (A4); `show_author` and `allow_export` must be answered explicitly
  (422 if missing); daily limit.

### Chunk 9.5 — Expert review and the shared atlas
- Atlas tab in `/review` (box corrections, added regions, decisions, two-expert quorum, diff of
  corrections), `/atlas` grid and photo page, coverage-quota dimension and view rate limits (A7),
  audit and metrics.
- **Tests:** quorum and optimistic locking; PROPOSED label blocks approval; corrected regions
  keep the member as author and record `corrected_by`; members see only APPROVED + ACTIVE (A5);
  pending photo → 404 for other members; an anonymous photo exposes no author name or id
  to other members (A12); domain and label filters; thumbnails-only listing, page size cap, coverage
  counter increments and the shield degrades a scraper (A7).

### Chunk 9.6 — Consent for atlas photos
- `ConsentService` contribution kinds, suspend/resume/revoke for atlas photos, „Mój wkład”
  list with thumbnails.
- **Tests:** suspend hides and deactivates points; resume only while APPROVED; revoke deletes the
  MinIO objects, wipes texts (CHECK), deletes points, is final, and marks exports (A6); only the
  author can act; expert-added regions follow the photo; switching `show_author` changes the
  atlas page at once; switching `allow_export` off excludes the photo from the next export.

### Chunk 9.7 — Dataset export
- `AtlasSettings`, export job (COCO / YOLO + manifest), admin API and a small „Eksport zbioru
  danych” panel in `/admin`.
- **Tests:** disabled by default → 404/403; step-up required (A9); only APPROVED + ACTIVE +
  `allow_export` included; COCO validates against the format (`pycocotools` in the test
  deps), YOLO boxes match the normalised coordinates; single-use link, expiry; manifest marks later revokes; audit event
  carries the manifest hash, not content.

### Phase B — recognition

### Chunk 9.8 — Visual embeddings and `atlas_visual_index`
- Pinned image-embedding model in the `models` volume, embedding engine in `worker-media`,
  collection bootstrap, outbox target `ATLAS` (approve/suspend/resume/revoke/merge), consistency
  check and `reindex` support, smoke check "image embedding separates unrelated images".
- Small benchmark script on the seed atlas (top-k label accuracy for whole image vs. regions),
  result recorded in the implementation notes; sets the default score threshold.
- **Tests:** payload has provenance and no text (A4); every consent transition reaches Qdrant
  through the outbox; mandatory filter in the repository (A5); model manifest verified offline;
  vector size mismatch refused at bootstrap.

### Chunk 9.9 — Recognition „Rozpoznaj”
- Recognition job (crops, search, label aggregation, VLM with references), `RECOGNITION`
  derivative, API, notebook UI with suggested boxes, accept/reject into annotations, citations.
- **Tests:** no similar examples → grounded "no verified examples" answer without a VLM call;
  `force_local_llm=True` asserted (A8); response contains no vectors or raw hits (A7); citations
  only to APPROVED + ACTIVE photos; suspended photo stops being cited immediately; accepted
  suggestions become `SUGGESTED` annotations; citations of anonymous photos carry no author
  (A12); search limited to the photo's domain unless widened; daily limit; injection guard on the VLM output.

### Chunk 9.10 — E2E, security review and docs
- Playwright scenario 11 (example domain from `seed-example`): member A adds a queen photo
  to a note, picks the domain „Pszczelarstwo”, marks the queen and a queen cell, sends it to
  the atlas anonymously (sees the scrubbed caption); two experts correct one box and
  approve; member B finds it in `/atlas` under *matka pszczela* shown as „Członek
  spółdzielni”; member B uploads another photo
  and „Rozpoznaj” suggests the queen with a citation; member A suspends the photo and it
  disappears from the atlas and from B's citations; admin export is refused while disabled.
- `SECURITY_REVIEW.md` (A1–A12: API1 BOLA on images, API4 resource limits, LLM01/LLM06, privacy
  of people in photos), README „Atlas”, story log delivery list.

**Stage 9 exit criteria:** Phase A (9.1–9.7) green and usable on its own; Phase B (9.8–9.10)
green; A1–A12 covered by tests; scenario 11 passes against the compose stack.

## 5. Decisions taken (change before 9.1 if you disagree)

| Topic | Decision | Alternative |
|---|---|---|
| Where photos are annotated | In the private notebook first, then contributed. | Directly in the shared atlas (no private draft; conflicts with Push-to-Wiki). |
| Unit of consent | The whole photo (one author). Expert-added regions follow it. | Per region (a member could revoke only their marks, leaving an image they don't own). |
| Shape | Rectangles, normalised coordinates. | Polygons/points (follow-up; the schema adds a `shape` column then). |
| Vocabulary | Domains (admin) with their own labels (experts govern, members propose); no production seed. | Free text tags (inconsistent labels make recognition fail); one flat vocabulary (name clashes across domains). |
| Author attribution | The author chooses per photo (name or „Członek spółdzielni”), can change it any time; `author_id` is always kept for provenance and consent. | Always shown like articles; always anonymous. |
| People in photos | VLM flag + member confirmation + expert check; no automatic blurring. | Automatic face blurring (follow-up; needs a face detector model). |
| Shared image storage | Encrypted per image (DEK + KEK), like private objects. | Plain MinIO SSE only (simpler, weaker at rest). |
| Dataset export | Off by default; admin, step-up, manifest, single-use link. Revoke cannot recall an export already downloaded; the manifest makes it traceable and the UI says so before exporting. | No export at all (Phase B only), or export to members (distillation risk). |
| Recognition | Similar-example retrieval + local VLM with references; strict grounding. | VLM alone (guesses without verified examples) or a trained detector (needs a training pipeline; the export enables it later). |
| Image embedding model | SigLIP base (pinned, CPU), confirmed by the 9.8 benchmark. | CLIP ViT-B/32 (smaller, weaker on fine detail), DINOv2 (no text side; fine if we never search by text). |

## 6. Product owner answers (2026-10-21)

1. **Domains:** the solution is multi-domain; beekeeping is only an example. → §3.1 domains,
   no production seed, example domain via `seed-example`.
2. **Author visibility:** the author decides per photo whether their name is shown. →
   `show_author`, A12, §3.4–3.6.
3. **Export opt-in:** yes, a separate opt-in per photo. → `allow_export`, A9, §3.4, §3.7.

---

## Implementation notes

### 9.1 (done)

- New package `spoldzielnia_ai/atlas/` (instead of `wiki/labels.py`): `text.py` (fold, slug,
  search key), `labels.py` (`LabelService`), `seed.py` (example domain).
- Migration `0014_visual_labels`: `visual_domain`, `visual_label` (+ enums
  `visual_domain_status`, `visual_label_status` with `REJECTED` for refused proposals). No seed.
- **Search without `unaccent`:** the extension needs a superuser on some installs, so labels
  carry a `search_key` (folded name + synonyms, Polish `ł` handled) matched with an escaped
  `ILIKE`. A rejected proposal keeps its row (audit) but frees its slug.
- **Merges are resolved on read** (`LabelService.resolve`/`usable`) instead of rewriting
  references: an expert can't (and mustn't) update a member's private annotations under RLS.
  Shared tables that reference labels register in `LABEL_REFERENCES` and are re-pointed in
  place (9.4). The merged label's name becomes a synonym of the target.
- API: `GET /atlas/domains`, `GET /atlas/labels`, `POST /atlas/labels/proposals` (members,
  10/day); `/atlas/curation/labels…` (EXPERT/ADMIN + MFA: list any status with proposer name,
  create, edit, approve, reject, merge, deprecate); `/admin/atlas/domains…` (ADMIN + MFA).
  Audit: `DOMAIN_CREATED/CHANGED`, `LABEL_PROPOSED/APPROVED/REJECTED/CHANGED/MERGED/DEPRECATED`.
- CLI `spoldzielnia atlas seed-example` (refused in prod, idempotent).
- Frontend: `lib/atlas.ts`, `LabelCombobox` (ARIA combobox, propose-in-place),
  `/review/labels` „Słownik etykiet” (linked from the review queue), `/admin/atlas` with
  „Dziedziny atlasu”, nav item „Atlas (admin)”.
- Tests: `tests/unit/test_atlas_text.py`, `tests/integration/test_atlas_labels.py` (authz
  matrix, diacritics search, per-domain uniqueness, private proposals + limit, merge/deprecate,
  archived domain, audit sequence, idempotent seed), `atlas-labels.test.tsx` (6).

### 9.2 (done)

- Migration `0015_image_annotation`: private `annotated_image` (domain, encrypted caption,
  `version`) and `image_annotation` (normalised box with the A11 CHECK, label, encrypted
  description, `source USER|SUGGESTED`), both RLS FORCE on `owner_id`; `note_attachment` +
  `width`, `height`, `preview_object_key`; `derivative_kind` + `ANNOTATIONS`.
- **Preview on upload, not in a worker:** the upload pipeline already decodes the image to
  sanitise it, so it also renders the ≤ 1600 px JPEG preview (`files/preview.py`) and stores it
  encrypted under the attachment's DEK with AAD `…|preview`. Photos from before 9.2 get their
  preview on first view (`notebook/images.py`, race-safe), so **no backfill CLI** is needed.
  Previews carry no metadata at all; the original keeps what the member chose. `size=full`
  serves the original, or a ≤ 4096 px JPEG for HEIC (browsers can't show HEIC). Note deletion
  removes previews too.
- `atlas/annotations.py` → `AnnotationService`: full-replace save with optimistic locking,
  label rules (usable in the photo's domain, own proposals allowed, a label already on the
  photo stays allowed after deprecation), archived domain → read-only, limits (50 marks,
  caption 2000, description 1000).
- **Notebook text:** each save rewrites one `ANNOTATIONS` derivative (status DONE, scan flags
  reset) and queues `annotations_saved` → `core.scan` → private index, like any derivative;
  an emptied photo drops it and its vectors. `OWNER_WRITTEN_KINDS` marks the kind as having no
  media task.
- API (`api/photos.py`): `GET …/attachments/{id}/image?size=preview|full` (A2 headers:
  `nosniff`, `inline`, `default-src 'none'; sandbox`, `private, no-store`, CORP same-origin),
  `GET`/`PUT …/attachments/{id}/annotations`.
- Tests: `tests/integration/test_photo_annotations.py` (11: safe serving, preview size and
  metadata, lazy preview, deletion, RLS for rows/bytes/no-user session, ciphertext at rest,
  save/load/409, box validation, limits, label rules, archived domain, derivative mirror).

### 9.3 (done)

- `components/annotator/ImageAnnotator.tsx`: marks are **positioned HTML boxes** (percent of
  the image) over the `<img>`, not an SVG overlay: equally crisp, each box is a focusable
  `role="button"`, and resize handles keep a fixed pixel size at any zoom. Pointer events
  (mouse, pen, touch; `touch-none` on the surface), pointer capture, drag-to-draw (a click or
  tiny drag makes no box), move, 8 resize handles, clamping to the photo and the 0.5 % minimum.
  Keyboard: Tab to a box, arrows move 1 % (Shift 0.2 %), Alt+arrows resize, Delete removes,
  Escape deselects. Zoom 100–400 % inside a scrolling viewport (pan by scrolling).
- `components/notebook/PhotoEditor.tsx` at `/notebook/[noteId]/photos/[attachmentId]`: domain
  first (changing it clears labels after a confirm), caption, numbered marks list (colour chip
  matching the box, `LabelCombobox` scoped to the domain, description, delete), save disabled
  until every mark has a label, `stale_version` → „Wczytaj ponownie”, archived domain →
  read-only, unsaved-changes guard (`beforeunload` + confirm on the back link), link to the
  full-resolution image.
- `PhotosPanel` in the note: thumbnails of the note's photos with „Oznacz: <plik>”.
- `lib/photos.ts`: queries, `toInput`/`toMarks`, `clampBox`, error copy.
- Tests: `photos.test.tsx` (10: draw/click, move to edge, handle resize, keyboard, read-only,
  clamp, save body with loaded version, 409 reload, domain-first + label required, archived,
  photo panel). The project has no axe tooling; accessibility is asserted through role and
  label queries (every control is reachable by its accessible name).

### 9.4 (done)

- Migration `0016_atlas`: private `atlas_submission` (RLS FORCE; `pii_stats` counts only),
  shared `atlas_image` (provenance, per-photo DEK, three object keys, `sha256`, 64-bit
  `phash`, scrubbed `caption`, `privacy_flags`, `allow_export`, `show_author`, `version`,
  tombstone CHECK), `atlas_region` (own `author_id` + `consent_status`, `corrected_by`, box
  CHECK, revoked-empty CHECK), `atlas_review` (created here for 9.5).
- **Two-step flow instead of one request:** `POST /atlas/submissions` only *prepares*
  (worker-core, task `promotion.prepare_atlas`); the member then reviews exactly what would be
  shared and `POST …/send` with **explicit** `show_author` and `allow_export` (no defaults in
  the API or the UI; `confirm_no_people` when the VLM saw people/faces), or `…/withdraw`. The
  choices are asked once the privacy flags are known, not before.
- `AtlasPreparer` (worker-core): decrypt the private original → three metadata-free JPEG
  copies (`atlas/images.py`, ≤ 4096/1600/320 px, each encrypted with its own AAD) → duplicate
  check in the domain (`sha256` or dHash distance ≤ `atlas.duplicate_max_distance`, default 6)
  → Presidio scrub of caption and descriptions → injection guard (quarantine → FAILED
  `injection_detected`, audited as `PROMPT_INJECTION_DETECTED`) → VLM privacy check
  (`atlas/privacy.py`, `PRIVACY_CHECK` prompt, local only; unavailable → recorded and shown,
  never "clean") → DRAFT atlas photo + regions in one transaction. A photo the experts sent
  back is re-prepared **in place** (same id, `version + 1`, old objects deleted); withdrawing
  that keeps it in REVISION_REQUIRED.
- `atlas/access.py` `can_view`: one rule for bytes now and pages/citations later (author any
  state; experts with MFA in review/approved; members approved + ACTIVE; nobody REVOKED).
  `GET /atlas/images/{id}/image?size=thumb|preview|full` uses it (404 otherwise).
- Label merges re-point `atlas_region` (`LABEL_REFERENCES`).
- `IMAGE_DESCRIPTION` prompt made domain-neutral (it said "pszczelarz"), new version.
- **worker-core gets object storage** (MinIO secret + `depends_on minio`): it reads the private
  original and writes the shared copies. Same root-credential TODO as worker-media.
- Limits: 20 submissions per member per day (`atlas.submissions_per_day`).
- Frontend: `AtlasSubmissionPanel` in the photo editor (disabled with a reason while unsaved),
  polling while preparing, review dialog with the shared copy and boxes, scrubbed caption and
  PII notice, privacy warnings, name/anonymous radio with no default, export opt-in unchecked.
- Tests: `test_atlas_submissions.py` (9: flow + metadata-free copies + provenance + A5 before
  approval + explicit choices + audit; request rules; daily limit; duplicate; injection;
  people confirmation and VLM outage; withdraw; re-prepare in place; merge re-points),
  `tests/unit/test_atlas_rules.py` (visibility matrix, flags, dHash), worker routing test,
  4 Vitest cases.

### 9.5 (done)

- `atlas/review.py` → `AtlasReviewService`: queue (IN_REVIEW + ACTIVE, oldest first),
  corrections (`PUT /atlas/review/{id}/regions`: full set, existing ids keep their author and
  get `corrected_by`, new marks are authored by the expert, removed marks are deleted, positions
  renumbered in two passes because of the unique key; ACTIVE labels only, an unchanged label may
  stay), decisions (`POST …/decision`: APPROVE needs no PROPOSED label and counts distinct
  experts **on the current version**, so a correction resets the quorum; REQUEST_CHANGES and
  REJECT need a comment; own photo → 403; `stale_version`/`not_in_review` → 409). Quorum is
  `governance.review_quorum` (shared with articles). Audit `ATLAS_CORRECTED`,
  `ATLAS_REVIEWED`, `ATLAS_STATUS_CHANGED`.
- **Deviation:** the plan's before/after overlay for the second approver is replaced by markers
  on each mark ("poprawione", "dodane przez eksperta") plus the review history, and by the quorum
  reset on any correction (the second approver always approves what they see).
- Reviewers never see the author (no id, no name, in queue or detail).
- `AtlasGallery` + `GET /atlas/photos` (approved + ACTIVE; filters domain, label (merge-aware),
  text over label names/synonyms and caption; page size capped by `atlas.page_size_max` = 48;
  thumbnails only) and `GET /atlas/photos/{id}` (`can_view`; author name only with
  `show_author`, `is_own` for the author, approving reviewers' names).
- **Coverage (A7):** `CoverageTracker` got a key `prefix`; `atlas/coverage.py` counts photos
  (approved + ACTIVE) under `covatlas:` with the shield's windows, limits and floor. A photo page
  and its preview/full image spend budget (HyperLogLog: the same photo counts once); thumbnails
  don't; authors and reviewing experts don't. Preview/full images of others' photos are also
  limited to `atlas.image_views_per_hour` (600). The corpus cache is invalidated on approval.
- Frontend: nav „Atlas” → `/atlas` gallery (domain/label/text filters, pagination, anonymous
  „Członek spółdzielni”), `/atlas/[id]` (boxes, numbered marks, author/reviewers, shield
  messages for 429/403), `/review/atlas` (queue with thumbnails, editable boxes and labels,
  „Zapisz poprawki” before deciding, decision form, privacy and pending-label notes, history),
  links between the review views.
- Tests: `test_atlas_review.py` (9: authz + author hidden from reviewers, quorum + duplicate
  approval + locking, own photo, corrections keep provenance and reset approvals, PROPOSED
  labels block approval, request changes/reject, gallery filters + A12, coverage throttle,
  image rate limit), `tests/fixtures/atlas.py` (shared helpers), 6 Vitest cases. Playwright
  scenario 11 started (Phase A part), finished in 9.10.

