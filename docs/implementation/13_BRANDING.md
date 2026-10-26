# Stage 13 — Organisation branding: name, logo and colours (US-07)

> **Story ([US-07](../USER_STORIES_LOG.md#us-07)):** as an administrator I want to configure the
> UI, so that I can set my organisation's logo and change its colours.

## Refined requirement

Refinement decisions (accepted 2026-10-26):

| Topic | Decision |
|---|---|
| What can be branded | **Organisation name** (menu, phone top bar, browser tab title, home page), **logo** (menu, phone top bar), **favicon** (generated from the logo). |
| Colours | The admin picks **two colours: brand and accent**. Every other shade (hover/strong, the text colour on a brand button, focus) is **derived**, and readable contrast (WCAG 2.2 AA) is **enforced**: an unreadable UI cannot be saved. Background, text, border and status colours (ok, warning, danger) stay fixed, so their meaning never changes. |
| Dark mode | Derived from the same two colours and checked against the dark background. One set of choices. |
| Defaults | Without any branding the UI is exactly as today: „Spółdzielnia AI”, the hexagon mark, the honey palette. „Przywróć domyślne” returns to that at any time. |
| Out of scope | The Keycloak sign-in page (a Keycloak theme: a separate story), fonts, per-user themes, a light/dark switch, custom CSS. |

## Acceptance criteria

```gherkin
Background: admin1 is logged in with MFA and opens /admin/branding ("Wygląd")

Scenario: set the organisation's name, logo and colours
  When admin1 enters the name "Pasieka Kowalscy", uploads logo.png and picks brand #1F6F4A and accent #C2410C
  Then the preview shows the menu, a button, a link and a badge in those colours, in light and dark
  When admin1 clicks "Zapisz"
  Then every member sees the new name and logo in the menu and the top bar, and the new colours,
    on their next page load, with no flash of the old colours
  And the browser tab shows "<page> · Pasieka Kowalscy" and the favicon made from the logo
  And the audit log records BRANDING_CHANGED (what changed; never the image bytes)

Scenario: a colour that would be unreadable
  When admin1 picks brand #FFF59D (light yellow)
  Then the preview shows the derived shades with their contrast ratios
  And the colours are adjusted until text on them reaches 4.5:1 (3:1 for large and UI parts),
    and the admin sees "Kolor przyciemniony, aby tekst był czytelny"
  And a colour that cannot reach the ratio at all is refused with the reason

Scenario: a logo that is not a plain image
  When admin1 uploads logo.svg, a GIF, a 12 MB PNG or an executable renamed to .png
  Then it is refused with the reason (only PNG, JPEG or WebP up to 2 MB; scanned; re-encoded)

Scenario: back to defaults
  When admin1 clicks "Przywróć domyślne" and confirms
  Then the name, logo, favicon and colours are the original ones for everyone

Scenario: not an administrator
  Then /admin/branding is "Brak dostępu", and every write answers 403 (401 / mfa_required as usual)
  But GET /api/branding works for everyone, signed in or not (the home page shows it)
```

## Design

### Colours: two inputs, derived tokens (rule R1)

`branding.palette.derive(brand, accent) -> Palette` (pure Python, unit tested) works in OKLCH:

| Token | Light | Dark |
|---|---|---|
| `--primary` | brand, lightness lowered until it reaches 3:1 against `--bg` (buttons, focus) | brand, lightness raised until 3:1 against the dark `--bg` |
| `--primary-strong` | darker step of brand, ≥ 4.5:1 against `--bg` and `--surface` (links, active menu text) | lighter step, ≥ 4.5:1 against the dark `--bg` |
| `--primary-contrast` | white or near-black, whichever gives ≥ 4.5:1 on `--primary` | the same rule |
| `--accent` | accent, adjusted to ≥ 3:1 against `--bg` | adjusted for dark |
| `--sidebar`, `--sidebar-accent` | a light tint of brand (hue kept, chroma ≤ 0.02) | a dark tint |

If no lightness of the hue reaches the ratio, the colour is refused (`422 contrast_unreachable`).
The derived palette, the contrast ratios and whether anything was adjusted are returned to the
admin before saving (`POST /admin/branding/preview`). The frontend never computes a palette: one
implementation, on the server.

### Applying it without breaking the CSP (rule R2)

- The root layout already renders per request. It reads the branding from the API
  (`GET /api/branding`, server to server through `API_INTERNAL_URL`, cached in the Next.js server
  for 30 s and keyed by the branding `version`) and renders **one `<style nonce={nonce}>`** with
  `:root { --primary: …; … }` and the same in the two dark-mode selectors. The CSP already allows
  nonced styles, so it stays unchanged and there is no flash of the default colours.
- Every value in that style block comes from the derived palette, which contains only
  `#rrggbb` strings. They are validated by Pydantic, by a database `CHECK` (`~ '^#[0-9a-f]{6}$'`)
  and again in the frontend before rendering (a non-matching value falls back to the default). No
  admin text ever reaches CSS.
- The admin preview changes CSS variables on a preview container through the CSSOM
  (`element.style.setProperty`), which the CSP does not restrict, and never through a `style=`
  attribute in HTML.
- The name is rendered as React text (escaped), up to 60 characters, with no control or bidi
  characters (`\p{C}` refused).

### Logo and favicon (rule R3)

- Upload: `POST /admin/branding/logo`, multipart, **PNG, JPEG or WebP only**. **SVG is refused**
  (it can carry script and external references), as are GIF and anything else, decided by content
  (`files.validation`, libmagic) and not by the name. Up to 2 MB and 4096×4096 px. ClamAV-scanned
  like every upload.
- Re-encoded with Pillow from pixels only (no metadata, no ICC beyond sRGB), into:
  `logo.png` (fits 512×128, transparency kept), `favicon-32.png`, `favicon-180.png` (square,
  padded on transparent). The original is never stored. Stored in object storage under
  `branding/<version>/`.
- Served at `GET /api/branding/logo/<version>.png` and `/api/branding/favicon/<size>/<version>.png`:
  public, `image/png`, `nosniff`, `Cache-Control: public, max-age=31536000, immutable` (the
  version in the URL changes on every save), with the API's deny-all CSP.

### Storage, audit and access

- Migration `0027_branding`: table `branding` with a single row (`id = 1` CHECK): `org_name`,
  `brand_hex`, `accent_hex`, `palette` (JSONB, derived), `logo_key`, `has_logo`, `version`
  (integer, +1 on every save), `updated_by`, `updated_at`. No row means the defaults.
- `BrandingService` (validate, derive, store, version); writes are ADMIN with an MFA session and
  CSRF, like every `/admin/*` write; rate limit of 30 changes an hour.
- Audit: `BRANDING_CHANGED` `{fields, version, logo_sha256?}`, `BRANDING_RESET` `{version}`.
- `GET /api/branding` is public (signed-out visitors see the home page): `{org_name, version,
  palette: {light, dark}, logo_url, favicon_urls}`. It holds nothing sensitive.

## Chunks

### 13.1 Model, palette derivation and service
- Migration `0027_branding` (singleton, hex CHECKs), SQLAlchemy model, `branding/palette.py`
  (OKLCH, WCAG contrast, derivation, `contrast_unreachable`), `BrandingService`, audit events.
- Tests: palette unit tests (honey stays as today; light yellow is darkened; mid grey fails or
  passes by the rule; every derived pair meets its ratio in light and dark), service and trigger
  integration tests.

### 13.2 Logo pipeline
- Validation by content (PNG/JPEG/WebP), size and pixel limits, ClamAV, Pillow re-encode, logo
  and favicon renditions, storage under the version, removal of older versions' files.
- Tests: SVG, GIF, a polyglot, an executable renamed to .png, an oversized and a decompression-bomb
  image are refused; EXIF and text chunks are gone from the output; renditions have the right
  sizes and transparency.

### 13.3 API
- `GET /api/branding` (public), `GET` logo/favicon files (public, immutable),
  `POST /admin/branding/preview`, `PUT /admin/branding`, `POST`/`DELETE /admin/branding/logo`,
  `POST /admin/branding/reset`; OpenAPI and `api-schema.d.ts` regenerated.
- Tests: authorization (member 403, no MFA `mfa_required`), CSRF, validation errors, rate limit,
  versioned URLs and cache headers, audit entries.

### 13.4 Branding applied in the app
- `lib/branding.ts` (server fetch with a 30 s cache and the defaults as a fallback when the API is
  unreachable), the nonced `<style>` in `app/layout.tsx`, the `title` template and `icons` from
  `generateMetadata`, the logo and name in `Sidebar`, the phone top bar and `HomeDashboard`
  (hexagon and „Spółdzielnia AI” when unset). `API_INTERNAL_URL` in compose (dev and prod).
- Tests: Vitest for the style block (only valid hex is rendered, anything else falls back), the
  logo/name fallbacks; E2E: no CSP violation with custom branding.

### 13.5 The „Wygląd” admin page
- `/admin/branding` and a „Wygląd” menu item (Administracja, icon `Palette`): name, brand and
  accent pickers (`<input type="color">` plus a hex field), a live preview in light and dark (menu,
  buttons, link, badge, a card) with each contrast ratio and an "adjusted" note, logo upload with
  its preview and „Usuń logo”, „Przywróć domyślne” with confirmation, „Zapisz”. Works on a phone
  like every page since Stage 12.
- Tests: Vitest for the form, the preview through the CSSOM, the errors and the reset.

### 13.6 E2E, security review, documentation
- E2E `15-branding.spec.ts`: admin1 brands the app; member1 sees the name, logo, favicon and
  colours on the next load; an SVG is refused; reset; a member gets 403; no CSP violation;
  axe passes with custom colours.
- Security review entry (CSS injection, SVG, image parsing, public endpoint, impersonation note),
  this document, the US-07 *Delivery* list.

## Risks and notes

- **Impersonation:** an administrator could brand the cooperative to look like another
  organisation. That is within an administrator's authority. Every change is audited with the
  author, and the sign-in page (Keycloak) stays unbranded in this story.
- **Image parsing is attack surface:** Pillow runs on admin uploads only, with
  `Image.MAX_IMAGE_PIXELS` limited, in the API process. If that is not acceptable, the re-encode
  moves to the media worker (as for notebook photos) at the cost of an asynchronous upload.
- **Contrast is enforced for the brand pair only.** The fixed tokens are already validated (6.6).
