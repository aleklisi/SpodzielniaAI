# Stage 9 — Visual Atlas (annotated photos, expert-verified dataset, image recognition)

**User story:** [US-03](../USER_STORIES_LOG.md#us-03) — *As a member I want to add a photo with a
description, mark one or more elements on it and describe them, so that the cooperative builds
verified knowledge for image recognition (e.g. a beekeeper adds photos of queen bees).*
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
2. **Label vocabulary:** experts curate labels (name, description, synonyms, category); members
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
- Per-cooperative or per-domain vocabularies (one shared vocabulary with categories).

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

## 3. Design

### 3.1 Label vocabulary (migration `0014_visual_labels`, shared zone)

```
visual_label  id, slug (unique), name (pl), description, category,
              synonyms text[], status PROPOSED|ACTIVE|DEPRECATED,
              merged_into_id → visual_label, proposed_by, approved_by, created_at, updated_at
```

- Seeded by a data migration with a starter beekeeping category (owner can edit before 9.1):
  *matka pszczela, robotnica, truteń, jajo, czerw otwarty, czerw kryty, matecznik, miseczka
  mateczna, pierzga, miód kryty, warroza (Varroa destructor), zgnilec (objawy), wosk / plaster*.
- `wiki/labels.py` → `LabelService`: `search` (name + synonyms, accent-insensitive via
  `unaccent`), `propose` (member, max 10 per day), `approve`, `edit`, `merge`, `deprecate`.
- API `/labels`: `GET` (members, ACTIVE + own PROPOSED), `POST /labels/proposals` (member),
  `POST /labels`, `PATCH /labels/{id}`, `POST /labels/{id}/merge`, `POST /labels/{id}/deprecate`
  (EXPERT/ADMIN + MFA). Audit `LABEL_PROPOSED`, `LABEL_APPROVED`, `LABEL_CHANGED`,
  `LABEL_MERGED`, `LABEL_DEPRECATED`.
- Frontend: label combobox (search, synonyms, "Zaproponuj nową etykietę"); experts get a
  „Słownik etykiet” tab in `/review`.

### 3.2 Private photo annotations (migration `0015_image_annotation`, private zone)

```
annotated_image   id, owner_id (RLS), note_id, attachment_id (unique, image only),
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
                       privacy_flags jsonb (kinds + scores only), allow_export bool,
                       version,
                       verified_at, review_alert, created_at
atlas_region (shared)  id, atlas_image_id, author_id, consent_status, label_id,
                       x, y, w, h, description, corrected_by, position
atlas_review (shared)  id, atlas_image_id, reviewer_id, decision, comment
```

- **Flow** (`POST /atlas/submissions {attachment_id, confirm_no_people: bool, allow_export:
  bool}`, max 20 per day): a worker job (`worker-core`, no network) decrypts the private image, re-encodes a shared
  copy (pixels only, max 4096 px, JPEG q90, preview 1600 px, thumbnail 320 px), computes
  SHA-256 and a perceptual hash (duplicate check against existing atlas photos → "to zdjęcie
  już jest w atlasie"), scrubs caption and descriptions (Presidio, `force_local_llm=True`),
  runs the injection guard on them, and asks the local VLM for `privacy_flags`. Result: an
  `atlas_image` in `IN_REVIEW` with its regions; the member sees the scrubbed texts and flags
  first and confirms (`SENT`) or withdraws.
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
- `/atlas` (members): grid of approved photos (thumbnails), filters by label and category,
  search by label/synonym; photo page with the image, boxes, caption, region descriptions,
  author display name (as on articles), verification date and reviewers.
- `ATLAS_SUBMITTED`, `ATLAS_REVIEWED`, `ATLAS_STATUS_CHANGED` audit events; metrics
  `atlas_submissions_total{outcome}`, `atlas_review_seconds`.

### 3.6 Consent for atlas photos

- `ConsentService` is generalised to "contribution kinds": `article_block` and `atlas_image`.
  Suspend/resume/revoke act on the whole photo (the image is one author's work). Regions added
  by an expert to a member's photo follow the photo.
- Suspend: hidden from `/atlas`, points `is_active=false`. Resume: back only if still
  `APPROVED`. Revoke: objects deleted from MinIO, caption/description wiped (tombstone, CHECK),
  points deleted, exports that contained it marked.
- „Mój wkład” lists atlas photos next to articles, with thumbnails, and a switch for
  `allow_export` (consent to dataset export, separate from consent to the atlas).

### 3.7 Dataset export

- `POST /admin/atlas/exports {format: coco|yolo, labels?: [..], min_verified_at?}` (A9) →
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
  search `atlas_visual_index` with the mandatory filter (A5), aggregate hits per label (score
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
- Migration `0014_visual_labels` with seed, `LabelService`, `/labels` API, audit events, label
  combobox component and the experts' „Słownik etykiet” tab.
- **Tests:** unit — slug/synonym search with Polish diacritics, proposal daily limit, merge
  re-points; integration — authz matrix (member proposes only; EXPERT without MFA → 403),
  deprecated label not selectable, audit rows; Vitest — combobox search, propose flow.

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
  provenance columns set (A4); daily limit.

### Chunk 9.5 — Expert review and the shared atlas
- Atlas tab in `/review` (box corrections, added regions, decisions, two-expert quorum, diff of
  corrections), `/atlas` grid and photo page, coverage-quota dimension and view rate limits (A7),
  audit and metrics.
- **Tests:** quorum and optimistic locking; PROPOSED label blocks approval; corrected regions
  keep the member as author and record `corrected_by`; members see only APPROVED + ACTIVE (A5);
  pending photo → 404 for other members; thumbnails-only listing, page size cap, coverage
  counter increments and the shield degrades a scraper (A7).

### Chunk 9.6 — Consent for atlas photos
- `ConsentService` contribution kinds, suspend/resume/revoke for atlas photos, „Mój wkład”
  list with thumbnails.
- **Tests:** suspend hides and deactivates points; resume only while APPROVED; revoke deletes the
  MinIO objects, wipes texts (CHECK), deletes points, is final, and marks exports (A6); only the
  author can act; expert-added regions follow the photo.

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
  suggestions become `SUGGESTED` annotations; daily limit; injection guard on the VLM output.

### Chunk 9.10 — E2E, security review and docs
- Playwright scenario 11: member A adds a queen photo to a note, marks the queen and a queen
  cell, sends it to the atlas (sees the scrubbed caption); two experts correct one box and
  approve; member B finds it in `/atlas` under *matka pszczela*; member B uploads another photo
  and „Rozpoznaj” suggests the queen with a citation; member A suspends the photo and it
  disappears from the atlas and from B's citations; admin export is refused while disabled.
- `SECURITY_REVIEW.md` (A1–A11: API1 BOLA on images, API4 resource limits, LLM01/LLM06, privacy
  of people in photos), README „Atlas”, story log delivery list.

**Stage 9 exit criteria:** Phase A (9.1–9.7) green and usable on its own; Phase B (9.8–9.10)
green; A1–A11 covered by tests; scenario 11 passes against the compose stack.

## 5. Decisions taken (change before 9.1 if you disagree)

| Topic | Decision | Alternative |
|---|---|---|
| Where photos are annotated | In the private notebook first, then contributed. | Directly in the shared atlas (no private draft; conflicts with Push-to-Wiki). |
| Unit of consent | The whole photo (one author). Expert-added regions follow it. | Per region (a member could revoke only their marks, leaving an image they don't own). |
| Shape | Rectangles, normalised coordinates. | Polygons/points (follow-up; the schema adds a `shape` column then). |
| Vocabulary | One shared vocabulary with categories, experts govern, members propose. | Free text tags (inconsistent labels make recognition fail). |
| People in photos | VLM flag + member confirmation + expert check; no automatic blurring. | Automatic face blurring (follow-up; needs a face detector model). |
| Shared image storage | Encrypted per image (DEK + KEK), like private objects. | Plain MinIO SSE only (simpler, weaker at rest). |
| Dataset export | Off by default; admin, step-up, manifest, single-use link. Revoke cannot recall an export already downloaded; the manifest makes it traceable and the UI says so before exporting. | No export at all (Phase B only), or export to members (distillation risk). |
| Recognition | Similar-example retrieval + local VLM with references; strict grounding. | VLM alone (guesses without verified examples) or a trained detector (needs a training pipeline; the export enables it later). |
| Image embedding model | SigLIP base (pinned, CPU), confirmed by the 9.8 benchmark. | CLIP ViT-B/32 (smaller, weaker on fine detail), DINOv2 (no text side; fine if we never search by text). |

## 6. Open questions for the product owner (do not block 9.1)

1. **Other domains:** the seed vocabulary is beekeeping only. Should categories for other
   cooperative domains be seeded too, or added by experts later?
2. **Who sees authors in the atlas:** shown like article authors (default), or anonymous
   ("członek spółdzielni") on photos?
3. **Export licence:** the manifest says "internal use of the cooperative". Do contributors need
   an explicit opt-in for dataset export (a checkbox when sending to the atlas), beyond consent
   to the atlas itself? Default in this plan: **yes**, a separate `allow_export` flag per photo,
   off unless ticked; only flagged photos are exported.

---

## Implementation notes

*(filled in per chunk while implementing)*
