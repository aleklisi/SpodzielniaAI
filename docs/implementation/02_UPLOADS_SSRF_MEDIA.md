# Stage 2 — Secure File Upload, SSRF Protection & Media Pipeline

**Goal:** members can safely put notes, files and links into their private notebook. Untrusted
content is validated, scanned, sanitised and processed in network-isolated workers.
**Spec refs:** `PLAN.md` Etap 2 · `ARCHITECTURE.md` §1 (encrypted private zone), §4 (ingestion)

---

## Chunk 2.1 — Private notes API & encrypted object storage

**Encryption at rest (gap G4)**: envelope encryption in `core/crypto.py`
- Per-user Data Encryption Key (DEK, 256-bit), wrapped by a Key Encryption Key (KEK).
- KEK source behind a `KeyProvider` protocol: `FileKeyProvider` reading the Docker secret
  `/run/secrets/kek` (dev + initial prod), later `VaultTransitKeyProvider`.
- AES-256-GCM via `cryptography`. AAD = `f"{table}:{row_id}:{owner_id}"` binds ciphertext to its row,
  so a ciphertext copied to another user's row fails to decrypt.
- Applies to `PrivateNote.body` and to attachment objects in MinIO (client-side encryption, plus
  MinIO SSE as a second layer).

**Models (migration `0004_private_zone`)**: all get the RLS owner policy from 1.4
- `NoteAttachment`: `id`, `owner_id`, `note_id`, `object_key`, `mime_type`, `size`, `sha256`,
  `scan_status (PENDING|CLEAN|INFECTED|REJECTED)`, `keep_location: bool`, `dek_wrapped`.
- `NoteDerivative`: `id`, `owner_id`, `attachment_id`, `kind (TRANSCRIPT|IMAGE_DESCRIPTION|PARSED_DOC|FETCHED_LINK)`,
  `content_ciphertext`, `status`, `injection_flags: JSONB` (used in Stage 3).

**Storage adapter** (`files/storage.py`)
- `ObjectStorage` protocol; `S3ObjectStorage` (aioboto3 / `miniopy-async`). Object keys are random
  UUIDs (no user-supplied names in paths). The original filename is kept only as encrypted metadata.

**API** (`api/notes.py`): every handler uses `get_rls_session`
- `POST /notes`, `GET /notes`, `GET /notes/{id}`, `PATCH /notes/{id}`, `DELETE /notes/{id}`.
- `GET /notes/{id}` for another user's note returns **404** (not 403, no existence oracle).

**Tests**
- Unit: crypto round-trip; AAD mismatch → `InvalidTag`.
- Integration: CRUD happy path; cross-user GET/PATCH/DELETE → 404 (BOLA test via HTTP).


**As built (decisions made during implementation)**
- **One DEK per encrypted row/object** (not per user): the schema already carries `dek_wrapped`
  on every encrypted row, and per-object keys limit the blast radius. Deleting the row destroys
  the key. The DEK is wrapped with AES-GCM under the KEK with AAD `dek|table:row_id:owner_id`.
  The wrapped form carries the KEK id (a fingerprint), so `StaticKeyProvider(kek,
  previous_b64=…)` supports rotation. The KEK is the Docker secret `kek` (`security.kek`).
- Field blobs carry their own nonce, and the field name is in the AAD (title ≠ body).
  `private_note.title` was plaintext in 1.3 and is now `title_ciphertext`. `body_nonce` was
  dropped (migration `0004_private_zone`).
- Attachments use a **segmented AEAD stream** (1 MiB segments, nonce prefix ‖ counter, index and
  final flag in the AAD) instead of one-shot GCM, so uploads of hundreds of MB never sit in
  memory, and truncation, reordering or extension is detected.
- `note_derivative` has `note_id` (required) and `attachment_id` (NULL for `FETCHED_LINK`),
  plus `status`, `error` (a machine code, never content), and `source_ciphertext` (e.g. the
  URL).
- Storage: `files/storage.py` `S3ObjectStorage` wraps the existing `minio` client in threads
  (lazy client: only processes that store files need the S3 secret). Keys are
  `att/<128 random bits>`. `MemoryObjectStorage` is for tests.
- API: `notebook/service.py` is the only place plaintext exists. `DELETE /notes/{id}` removes
  the storage objects in a background task, i.e. after the RLS transaction committed. Limits:
  title ≤ 200 chars, body ≤ 200 000.

---

## Chunk 2.2 — File type validation & archive guard

**Magic-bytes validation** (`files/validation.py`, adapter `files/magic_adapter.py`)
- `uv add python-magic` (needs `libmagic1`: already in the `api` image base layer; for host-side
  tests on the Mac, `brew install libmagic`).
- Allow-list (not deny-list) mapped to canonical extensions:
  `application/pdf`, DOCX/ODT, `text/plain`, `text/markdown`, `image/jpeg|png|webp|heic`,
  `audio/mpeg|wav|ogg|mp4|x-m4a`, `video/mp4` (audio track only), `application/zip` (only as container, see below).
- Rules:
  1. Detected MIME must be in the allow-list → else **415**.
  2. Declared extension and `Content-Type` must agree with the detected MIME → else **415**.
  3. Executables (`application/x-dosexec`, ELF, Mach-O, scripts with shebang) are always rejected,
     whatever the extension.
  4. OOXML (DOCX) is a ZIP, so it goes through the archive guard and is then checked for
     `vbaProject.bin` (macros) → reject.
- Streaming upload with a hard size limit (`max_upload_bytes`, e.g. 200 MB audio / 50 MB doc). Bytes are
  counted while streaming to a spooled temp file, never trusting `Content-Length`.

**Archive guard** (`files/archive_guard.py`)
- Limits: max nesting depth **1** (an archive inside an archive is rejected), max entries (1000),
  max total uncompressed size (500 MB), max per-entry compression ratio (100:1), plus a check
  for path traversal (`..`, absolute paths) and symlinks.
- Inspect metadata first, then **stream-decompress with a byte counter** (metadata can lie).

**Tests (mirror PLAN verification)**
- `test_exe_renamed_pdf` → HTTP **415** (fixture: minimal PE header bytes `MZ…` saved as `invoice.pdf`).
- ELF renamed `.jpg` → 415. Polyglot with a valid PDF header but `.exe` extension → 415 (mismatch).
- Zip bomb fixtures generated at test time (42.zip-style nested, and high-ratio single entry) → rejected.
- DOCX with macros → rejected. Valid PDF/PNG/MP3 → accepted.


**As built (decisions made during implementation)**
- `files/validation.py` keys the allow-list by the MIME type libmagic reports and maps it to a
  canonical stored type (`audio/x-wav` → `audio/wav`, `audio/x-m4a` → `audio/mp4`, `.md`
  stays `text/markdown`). A declared `Content-Type` must be the detected type, a listed alias,
  or the generic `application/octet-stream` (a browser that makes no claim). Size limits per
  category: documents 50 MB, text 5 MB, images 25 MB, audio/video 200 MB, ZIP 50 MB.
- Executables are refused by our own signatures (PE, ELF, Mach-O/Java class, shebang) **and**
  by libmagic's executable/script types. libmagic alone misses truncated ELF headers.
- Text must be valid UTF-8. SVG/HTML are not on the allow-list (active content).
- Archive guard: per-entry ratio is checked only above 1 MiB uncompressed (tiny entries
  legitimately compress >100:1, and the total limit bounds them). Encrypted entries are
  refused because they can't be scanned. Entries are also refused when they are executables,
  and when they are archives (gzip/bzip2/xz/7z/rar/zstd/cab/tar/zip), i.e. nesting depth 1.
  OOXML with embedded `.docx` objects is therefore refused too, which is acceptable.
- Macros: DOCX with `vbaProject.bin`/`vbaData.xml`, ODT with `Basic/` or `Scripts/`.
- Host tests need libmagic (`brew install libmagic`). `make smoke` re-checks the image's
  Debian libmagic (exe-as-pdf refused, PDF accepted).

---

## Chunk 2.3 — ClamAV scanning, EXIF stripping & upload endpoint

**ClamAV** (`files/antivirus.py`)
- Async clamd `INSTREAM` client (small hand-written typed client over `asyncio.open_connection`;
  avoids untyped `pyclamd`). Chunked streaming with a size limit matching clamd `StreamMaxLength`.
- Fail-closed: clamd unavailable or timeout → upload rejected (503), never stored unscanned.

**EXIF / metadata stripping** (`files/sanitize.py`)
- Images: `Pillow` re-encode without EXIF/XMP/IPTC, applying the orientation transform first.
  HEIC via `pillow-heif`.
- If `keep_location=true`: preserve **only** GPS tags (still strip device serial, owner name, etc.).
  The choice is audited (`ATTACHMENT_LOCATION_RETAINED`).
- PDFs: strip document info / XMP metadata (`pikepdf`). Audio: strip ID3 tags (`mutagen`).

**Endpoint** `POST /notes/{note_id}/attachments` (multipart, field `keep_location: bool = False`)
Caddy's `request_body max_size` is set to the largest upload limit, so oversized bodies are cut at
the proxy before reaching `api`. Uploads spool to the `api` container's `tmpfs` (size it to the limit).
Order of operations (each failure → specific status, audited as `UPLOAD_REJECTED` with reason):
1. Auth + RLS ownership of `note_id`.
2. Stream to temp with size limit (413).
3. Magic-bytes + extension validation (415).
4. Archive guard if a container (422).
5. ClamAV scan (422 `infected`, 503 scanner down).
6. Metadata sanitisation.
7. Encrypt → MinIO → `NoteAttachment(scan_status=CLEAN)`.
8. Enqueue the processing task (2.5). Return `202 Accepted` with the attachment ID.

**Tests**
- EICAR test string upload → 422 + audit entry (integration with real clamd; unit with fake scanner).
- Scanner down → 503, nothing in storage.
- JPEG with GPS EXIF → stored image has no GPS. With `keep_location=true`, GPS stays and other tags are gone.


**As built (decisions made during implementation)**
- Upload streaming: `files/multipart.py` parses `request.stream()` with python-multipart's
  callback parser. Starlette's `request.form()` would buffer the whole file first, with no cap
  on file parts. One `file` part, known small fields only (`keep_location`). The cap is
  `uploads.max_bytes` (200 MB) while streaming, and the per-type limits come after detection.
  Filenames are reduced to a printable base name and stored only encrypted.
- No DB transaction is held while bytes stream or are scanned. There is a short RLS check of
  the note before reading the body (404 without consuming it), then processing, then a short
  RLS transaction inserting the row. It re-checks the note and deletes the stored object if
  the note vanished or the insert failed.
- `UPLOAD_REJECTED` is written by the session audit sink in its own transaction, so it
  survives the failed request. Payload: reason, note id, size or MIME type or signature. Never
  the filename. `ATTACHMENT_LOCATION_RETAINED` is in the insert transaction.
- clamd: `CLAMD_CONF_StreamMaxLength/MaxFileSize=210M`, `MaxScanSize=400M`,
  `AlertExceedsMax=yes`. Without the latter, clamd answers `OK` for files beyond its limits
  without scanning them. The client refuses anything over `clamav.stream_max_bytes` itself.
- Sanitising: images are re-encoded after `exif_transpose`. Only the ICC profile is kept, plus
  the GPS IFD with `keep_location`. Decompression bombs are refused (`uploads.max_image_pixels`).
  Encrypted PDFs are refused (they can't be inspected). DOCX/ODT/ZIP/text pass unchanged
  (document text is scrubbed for PII in Stage 3).
- Concurrency: `uploads.max_concurrent` (2) uploads at a time. The api `/tmp` tmpfs is 1 GB
  (spool + sanitised copy + ciphertext ≈ 2× file). Caddy `request_body max_size 210MB`.
- `GET /notes/{id}/attachments/{aid}` (attachment + its derivatives) is already here. The
  processing queue is a no-op until 2.5.

---

## Chunk 2.4 — SSRF-safe link fetcher

**Module** `net/safe_fetch.py` for `POST /notes/{id}/links {url}`
- Schemes: `https` (and `http` only if configured). Reject `file:`, `gopher:`, `ftp:`, `data:`, etc.
- Reject credentials in the URL (`user:pass@`). Normalise IDNA hostnames.
- **Resolve DNS once** (`loop.getaddrinfo`), validate **every** resolved IP, then connect **to the
  validated IP** with the original `Host` header / SNI. This pins against DNS-rebinding (custom
  `httpx` transport with `server_hostname` set).
- Blocked ranges (use `ipaddress` `is_private / is_loopback / is_link_local / is_reserved /
  is_multicast / is_unspecified` **plus** explicit list):
  `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`
  (covers `169.254.169.254`), `100.64.0.0/10`, `0.0.0.0/8`, `::1`, `fc00::/7`, `fe80::/10`,
  IPv4-mapped IPv6 (`::ffff:127.0.0.1`) and NAT64 forms.
- Reject decimal/octal/hex IP encodings (`http://2130706433`, `http://0x7f.1`). Parse via
  `ipaddress` after normalisation.
- Redirects: followed **manually**, max 3, each hop re-validated.
- Limits: connect timeout 5s, total 15s, max body 5 MB, allowed response content types (HTML, PDF, text).
- Fetch runs in the **`worker-egress` container** only (2.5), never in the API process. The `api`
  container is on internal networks only and physically can't make outbound connections.
- Prod defence in depth: a host `DOCKER-USER` iptables/nftables rule drops traffic from the `egress`
  subnet to RFC1918, link-local and the Docker subnets (`infra/scripts/egress-firewall.sh`).
- Result is converted to Markdown (HTML via `trafilatura`/`markdownify`), stored as a
  `NoteDerivative(kind=FETCHED_LINK)`, then goes through Stage 3 injection scanning.

**Tests (mirror PLAN verification)**
- `http://127.0.0.1:8000`, `http://169.254.169.254/latest/meta-data`, `http://[::1]`,
  `http://[::ffff:127.0.0.1]`, `http://2130706433`, `http://localhost`, `http://0.0.0.0` → **blocked** (400 `ssrf_blocked`).
- Public host whose DNS resolves to `10.0.0.5` (monkeypatched resolver) → blocked.
- Public URL redirecting to `http://169.254.169.254` → blocked at hop 2.
- DNS rebinding: resolver returns public IP first, private second. The connection uses the pinned first IP only.

---

## Chunk 2.5 — Celery media pipeline in network-isolated workers

**Celery app** (`workers/celery_app.py`)
- Broker/result: Redis (separate DB indexes). JSON serializer only (**no pickle**), `task_acks_late`,
  `worker_prefetch_multiplier=1`, time limits per task, `task_reject_on_worker_lost`.
- Typed task wrapper: tasks take **only IDs** (`attachment_id`, `owner_id`), never content. The
  worker re-loads data through `rls_session(owner_id)`.
- Queues: `media.transcribe`, `media.vision`, `media.parse`, `egress.fetch`, `index` (Stage 4).

**Tasks** (each in its own adapter module):
| Task | Engine | Output |
|---|---|---|
| `transcribe_audio` | `faster-whisper` (model `large-v3` / `medium`, `language="pl"`, CPU int8 in dev) | `TRANSCRIPT` |
| `describe_image` | local VLM via `LLMGateway` (`force_local_llm=True`) (stub until 3.4) | `IMAGE_DESCRIPTION` |
| `parse_document` | `Docling` PDF/DOCX → Markdown | `PARSED_DOC` |
| `fetch_link` | `safe_fetch` (2.4) | `FETCHED_LINK` |

**Images & compose services** (see DEPLOYMENT_DOCKER §2–§3)
- New Dockerfile target **`worker-media`**: `api` base + extra `media` (`uv add --optional media
  faster-whisper docling pymupdf`) + `ffmpeg`. Service `worker-media` runs
  `spoldzielnia worker --queues media.transcribe,media.vision,media.parse`, **only on `backend`**
  (internal, no internet).
- Service `worker-egress` reuses the **`api` image** with `spoldzielnia worker --queues egress.fetch`,
  on `backend` + `egress`.
- Models: `models-init` one-shot (profile `tools`, `make models`) downloads pinned Whisper
  (CTranslate2) and Docling weights with SHA-256 verification into the `models` volume. Workers mount
  it read-only at `/models` with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`. Dev uses whisper
  `small` int8 (CPU, Mac), prod uses `large-v3` (GPU via `compose.gpu.yaml`).
- All worker containers get the hardening anchor (non-root, `read_only` + tmpfs, `cap_drop: [ALL]`,
  `no-new-privileges`) plus memory/CPU limits.
- `compose.dev.yaml`: `develop.watch` restarts workers on `src/` changes.

**Status & API**
- `GET /notes/{id}/attachments/{aid}` shows processing status; derivatives are listed on the note.
- Failure handling: retries with backoff for transient errors. Permanent failure → status `FAILED`,
  audit entry, user-visible message.

**Tests**
- Unit: Celery in `task_always_eager` mode with fake engines (whisper/docling/VLM fakes return fixtures).
- Unit: a task that receives content instead of IDs fails type-check (pyright) / is rejected at runtime.
- `ml`-marked: real `faster-whisper` `tiny` model on a 3-second Polish WAV fixture; real Docling on a small PDF.
- Docker gate (`make smoke`, extended): from inside `worker-media`, an outbound connection to
  `1.1.1.1:443` **fails**. From `worker-egress` it succeeds. Uploading a short WAV through
  `https://spoldzielnia.localhost/api/...` produces a `TRANSCRIPT` derivative end-to-end in the stack.

**Stage 2 exit criteria:** exe-as-pdf → 415, SSRF matrix blocked, EICAR rejected, media tasks
produce derivatives, isolation smoke test passes.
