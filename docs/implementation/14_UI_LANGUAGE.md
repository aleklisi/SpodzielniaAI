# Stage 14 — UI language: Polish and English (US-08)

> **Story ([US-08](../USER_STORIES_LOG.md#us-08)):** as a user I want to select the UI language,
> so that I can use the application in English. Given a user, when the user switches the
> language, the UI reloads and the interface is in the chosen language.

## Refined requirement

Refinement decisions (accepted 2026-10-26):

| Topic | Decision |
|---|---|
| Languages | **Polish** (`pl`, the default) and **English** (`en`). The catalogue structure allows a third language later, but none is planned. |
| What changes | **Every screen of the application**: menu, page titles, buttons, forms, empty states, dialogs, error and status messages, `aria-*` labels, the tab title; **dates and numbers** (`Intl` with the locale); **the server's fixed texts** (the chat's „Brak oficjalnego stanowiska…”, the verbatim refusal, „Członek spółdzielni”, the revision alert, the „Twoje dane” page descriptions). **Chat answers** (cooperative chat and the notebook's „Zapytaj”) are written in the user's chosen language. |
| What does not change | **Content**: notes, wiki articles, atlas labels and domains, the organisation's name, audit log details. Push-to-Wiki drafts stay Polish (the knowledge base has one language). The **Keycloak sign-in and OTP pages** stay as they are (a Keycloak theme: a separate story). |
| Where the choice lives | **On the account** (`users.locale`), so it follows the user to every device. A `lang` cookie applies it on the first paint of every page; the server sets it at sign-in from the account. |
| Default | **Polish**, always, until the user chooses. No `Accept-Language` detection, no admin default. |
| Switching | A language switch in the menu (both names in their own language: „Polski”, „English”). Choosing one saves it to the account, sets the cookie and **reloads the page**; the user stays on the same page, now in the new language. Signed-out visitors can switch too (cookie only); their choice is saved to the account at the next sign-in if the account has none yet. |
| URLs | Unchanged. No `/en/...` prefixes: the language is a preference, not a route. |

One narrowing against the question asked during refinement: chat answers follow the **chosen
language**, not the language each question is typed in. The anti-distillation checks (R4) must
know the answer's language before the model writes it, and a fixed choice per user is
predictable. A question typed in English under the Polish UI gets a Polish answer.

## Acceptance criteria

```gherkin
Background: member1 is signed in; the UI is in Polish (no choice made yet)

Scenario: switch to English
  When member1 opens the menu and chooses "English" in "Język"
  Then the page reloads on the same address
  And the menu, the page, its buttons, dialogs, messages and the tab title are in English
  And <html lang="en">, and dates read like "26 Oct 2026"
  And the notes, articles and atlas labels are unchanged

Scenario: the choice follows the account
  Given member1 chose English
  When member1 signs in on another browser
  Then the UI is in English from the first page, with no flash of Polish

Scenario: server texts and answers in English
  Given member1 chose English
  When member1 asks the chat a question the knowledge base cannot answer
  Then the answer is "No official position of the cooperative's experts on this matter. …"
  When member1 asks a question the knowledge base answers
  Then the answer is written in English, with the same citations, and passes the same verbatim
    and watermark checks as a Polish answer

Scenario: back to Polish
  When member1 chooses "Polski"
  Then the page reloads in Polish

Scenario: signed out
  When a visitor chooses "English" on the home page
  Then the home page is in English (the Keycloak sign-in page is not)
  And after signing in, an account with no choice yet keeps English and saves it

Scenario: nothing left untranslated
  Then every user-visible string in the frontend comes from the message catalogues,
    the Polish and English catalogues have the same keys and the same placeholders,
    and every existing E2E scenario still passes in Polish
```

## Design

### Locale model (rule R1)

- Migration `0028_user_locale`: `users.locale text NULL` with `CHECK (locale IN ('pl','en'))`.
  `NULL` means "not chosen" and is shown as Polish.
- `GET /me` returns `locale` (`"pl"` when NULL) and `locale_chosen`. `PUT /me/locale`
  `{locale}`: an authenticated user, CSRF as for every write, a rate limit of 30 an hour. It
  answers with `Set-Cookie: lang=<locale>; Path=/; Max-Age=31536000; SameSite=Lax; Secure`. No
  audit event: a display preference, not a security fact.
- The **`lang` cookie** holds `pl` or `en` and nothing else. Every reader (the Next.js layout,
  the API) accepts only those two values and treats anything else as `pl`. It is not HttpOnly,
  so a signed-out visitor's switch can set it from the browser, as the `sb` menu cookie does.
- **Sign-in** (`/auth/callback`): if the account has a locale, the callback sets the cookie from
  it. If not, and a valid `lang` cookie came in, that choice is saved to the account. So a new
  device is right on its first page and nothing reloads in a loop.
- The **API's language** for its own texts is the account's locale for a signed-in user and the
  cookie's for a signed-out one. It never uses `Accept-Language`.

### Frontend catalogues (rule R2)

- **`next-intl` 4.14.9** (pinned exact; released 2026-10-02) in its mode *without i18n
  routing*. `src/i18n/request.ts` reads the `lang` cookie through `cookies()` and loads
  `messages/{pl,en}.json`. The root layout sets `<html lang>`, wraps the app in
  `NextIntlClientProvider` and builds the title template from the catalogue. Server components
  use `getTranslations`, client components `useTranslations`.
- **Messages are ICU** (`{count, plural, one {# konto} few {# konta} many {# kont} other {# konta}}`),
  which replaces `lib/plural.ts`. Namespaces follow the areas: `shell`, `home`, `notebook`,
  `wiki`, `chat`, `atlas`, `review`, `contributions`, `privacy`, `admin.llm`, `admin.users`,
  `admin.security`, `admin.atlas`, `admin.branding`, `errors` (the code maps from
  `lib/messages.ts`, `brandingError` …), `common`.
- **Dates and numbers** go through `useFormatter()` / `getFormatter()` with named formats
  (`short`, `medium`, `long`, `dateTime`), which replaces the ~20 `Intl.DateTimeFormat("pl-PL")`
  and `toLocaleString("pl-PL")` calls. Case-insensitive comparisons (`LabelCombobox`) use the
  active locale.
- **Rich text** (a link or a `<strong>` inside a sentence) uses `t.rich`, never HTML in a message:
  messages are text, rendered as React text, so the CSP and Trusted Types are unchanged.
- **Glossary** for consistent English: Spółdzielnia → cooperative, Rada Ekspertów → Expert
  Council, Notatnik → Notebook, Wiki → Wiki, Atlas → Atlas, wniosek do Wiki → wiki proposal,
  szkic → draft, kanoniczny → canonical, recenzja → review, zgoda → consent, „Twoje dane” →
  „Your data”, „Wygląd” → „Appearance”. It lives at the top of `messages/README.md`.
- The default organisation name „Spółdzielnia AI” is a brand name and is not translated.

### Switching (rule R3)

- `components/shell/LanguageSwitch.tsx` sits in the menu footer, in the phone drawer and, in
  the collapsed rail, as a globe icon with a menu. It is a radio group: each option has its own
  `lang` attribute so a screen reader pronounces „English” and „Polski” correctly.
- On choice: `PUT /me/locale` (signed in) or the cookie alone (signed out), then
  `window.location.reload()`. Unsaved work is protected as it is today: the notebook autosaves,
  and a dirty form asks before leaving (`beforeunload`).
- If the API call fails, nothing changes and the error is shown in the current language.

### Server texts and answers (rule R4)

- `spoldzielnia_ai/i18n/` holds `Locale = Literal["pl","en"]`, `catalog.py` with every fixed
  user-facing text in both languages (`t(key, locale, **params)`) and `locale_for(principal,
  request)`. A unit test checks that both languages have the same keys and placeholders.
- **Stored texts become codes.** `articles.review_alert` currently stores the Polish sentence:
  migration `0028` converts it to the code `revision_required`, and the frontend renders it.
  Error `detail`s stay English machine codes, as today, and the frontend maps them.
- The **„Twoje dane” page** (`/api/processors`) returns purpose and engine descriptions in the
  caller's language.
- **Chat answers in English** (`rag.synthesis`, the notebook's Q&A): the system prompt says
  which language to answer in. The rest of the prompt, and the sources, stay as they are.
  Anti-distillation must not weaken for English answers (AGENTS.md rule 3):
  - **Watermark:** English slot pairs (`should/ought to`, `moreover/furthermore`,
    `important/essential`, `also/additionally` …), at least as many as the Polish set, chosen by
    the answer's language. The structural slots are already language-free. The detector also
    tries both sets.
  - **Verbatim check across languages:** a translation slips past the 5-gram and ROUGE-L checks,
    which compare Polish words. For an English answer the `OutputVerifier` also compares the
    answer's sentences with the source blocks by **embedding similarity** (the configured
    embedding model). The answer is refused when too many sentences of one block are matched
    above a threshold (`verbatim_xlang_*` settings, calibrated on test pairs). If no embedding
    model is available, **English answers are switched off** and the answer is written in
    Polish with a note in English. It never runs unchecked.
  - `extraction_intent` and the injection rules get English phrasings („quote in full”,
    „word for word”, „ignore previous instructions” …), where missing.
  - The fixed texts (`NO_OFFICIAL_POSITION`, `NO_NOTES_ANSWER`, `REFUSAL`, `UNKNOWN_AUTHOR`,
    `UNKNOWN_EXPERT`) come from the catalogue.
- The model that writes answers is the one configured for RAG. Answering in English needs no
  new provider and sends nothing new to it, so the private-data rule (AGENTS.md rule 4) is
  unchanged.

### No hard-coded strings (rule R5)

- A Vitest check walks `src/**/*.tsx?` (tests excluded) and fails on Polish letters
  (`ąćęłńóśźż`) in string literals or JSX text outside `messages/`, with a short, reasoned
  allowlist (for example the default brand name „Spółdzielnia AI”).
- `eslint-plugin-i18next`'s `no-literal-string` rule (JSX text and attributes like `aria-label`,
  `title`, `placeholder`, `alt`) catches strings without diacritics.
- A catalogue test: `pl.json` and `en.json` have the same keys, every ICU message parses, the
  placeholders match, and no English value is empty or equal to the Polish one (an allowlist
  covers names such as „Wiki”).

## Chunks

### 14.1 Locale on the account and the server's texts
- Migration `0028_user_locale` (`users.locale` + CHECK; `review_alert` to a code), `GET /me` with
  `locale`, `PUT /me/locale` (CSRF, rate limit, `Set-Cookie`), the cookie set or adopted at sign-in,
  `spoldzielnia_ai/i18n` (catalogue, `locale_for`), fixed texts and the processors' descriptions
  from the catalogue; OpenAPI and `api-schema.d.ts` regenerated.
- Tests: the CHECK refuses `de`; the cookie only ever carries `pl`/`en`; a bad cookie is Polish;
  sign-in sets the cookie from the account or adopts the visitor's choice; the catalogue's parity;
  the refusal texts in each language; `review_alert` migrated both ways.

### 14.2 Frontend i18n foundation and the language switch
- `next-intl` (exact pin), `src/i18n/request.ts`, `messages/pl.json` + `en.json` + README
  (glossary), the layout (`<html lang>`, provider, title template), named date and number
  formats, a test wrapper with the provider for Vitest, `LanguageSwitch` in the menu, drawer and
  rail, the save → cookie → reload flow.
- The shell (`AppShell`, `Sidebar`, `nav-items`, `BrandMark` alt), error pages and `HomeDashboard`
  are moved to the catalogue in this chunk, so the switch is visible end to end.
- Tests: the switch saves and reloads; the rail menu works by keyboard; `lang` on the options;
  the layout renders English from the cookie; the catalogue parity and ICU test.

### 14.3 Member pages to the catalogue
- Notebook (list, workspace, sources, attachments, links, recordings, recognition), wiki (list,
  article, drafts, consent), chat (including the proof-of-work text and example questions), atlas
  (gallery, photo, annotation editor, label combobox), contributions, „Twoje dane”, the code-to-
  message maps of `lib/messages.ts` and `lib/plural.ts`.
- Tests: existing component tests pass with the Polish catalogue; each area has an English render
  test of its main screen.

### 14.4 Review and admin consoles to the catalogue
- Review (queue, details, decision), `/admin/llm` (catalogue, routes, history, index, playground),
  `/admin/users`, `/admin/security`, `/admin/atlas`, `/admin/branding` (including
  `brandingError`).
- Tests as in 14.3; the no-hard-coded-strings check and the ESLint rule are switched on at the end
  of this chunk and pass with an empty backlog.

### 14.5 Answers in English without weakening anti-distillation
- Answer language in the RAG and notebook Q&A prompts, English watermark slots and detection, the
  cross-language verbatim check (embedding similarity, settings, fail-closed without an embedding
  model), English extraction-intent and injection phrasings.
- Tests: an English translation of a source block is refused; a genuine English synthesis passes;
  watermark bits are stable per account in English and the detector finds the account; without an
  embedding model an English user gets a Polish answer with the note; the contract test (no raw
  chunks in any response) unchanged.

### 14.6 E2E, security review, documentation
- E2E scenario 16 (`16-language.spec.ts`): member1 switches to English (reload, `lang="en"`, menu
  and page in English, dates); a second browser context signs in and is English on the first
  page; the chat's "no official position" answer in English; no CSP violation; axe in English;
  back to Polish. A signed-out visitor's switch. Existing scenarios 1–15 keep running in Polish.
- `SECURITY_REVIEW.md` (the cookie's values, translation as a distillation channel, the
  watermark's English slots), `ARCHITECTURE.md` (i18n), US-08 marked DONE.

## Risks and notes

- **Size:** about 800 lines across ~86 frontend files carry Polish text, plus strings without
  diacritics. Chunks 14.3 and 14.4 are mechanical but long. The lint rule and the diacritics check
  keep regressions out after that.
- **Translation quality:** the English catalogue is written with the glossary and reviewed by the
  product owner before 14.6 is ticked. Strings longer in English are checked at 360 px by the
  existing no-horizontal-scroll test, run with the English locale too.
- **Cross-language verbatim check:** the thresholds need calibration on real pairs. Until then,
  they start strict: a borderline English answer is refused rather than let through.
- **Model quality:** smaller models write weaker English. The RAG model is the admin's choice
  (`/admin/llm`), so this is configuration, not code.
- **Keycloak** pages stay Polish. A follow-up can pass `ui_locales` to Keycloak and enable its
  English theme.
