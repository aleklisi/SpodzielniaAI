# Stage 12 — Open Notebook–style app shell and mobile support (US-06)

> **Story ([US-06](../USER_STORIES_LOG.md#us-06)):** as a member I want the application to look
> and work like Open Notebook (github.com/lfnovo/open-notebook): a menu on the left and the
> workspace on the right, taking most of the window, on a desktop and on a phone.

## Refined requirement

The original request: "UI should be like in OpenNotebook; menu on the left side; workspace on the
right (2/3) of the visible window; the UI has to support mobile devices."

Refinement decisions (accepted 2026-10-26):

| Topic | Decision |
|---|---|
| Menu width | Fixed **16rem** sidebar that collapses to a **4rem icon rail**, as in Open Notebook. The workspace fills the rest: ≥ 2/3 of the window at every desktop width (≈ 80 % at 1280px, 94 % when collapsed). A literal 1/3 menu was rejected: about 480px of mostly empty space at 1440px. |
| Scope | **New shell for every page, plus a restyle of every page** (notebook, wiki, atlas, chat, contributions, privacy, review, admin) using shared building blocks. |
| Phone navigation | **Drawer from the left**: a top bar with ☰ opens the same menu as a slide-in drawer. |
| Notebook | The note page uses Open Notebook's columns, **Źródła \| Notatka \| Zapytaj**. On a phone they become three tabs. |
| What we take from Open Notebook | The layout and interaction patterns only. No code, logo or branding is copied: the cooperative palette (honey, heather) and the Inter/Outfit fonts stay. |
| Out of scope | A theme switch (the UI keeps following the system setting), a ⌘K command palette, a language switch (the UI is Polish only), PWA/offline. These can be separate stories later. |

## Layout

```
Desktop ≥ 1024px (lg)                        Tablet 768–1023px (md)        Phone < 768px
┌──────────┬────────────────────────────┐   ┌────┬─────────────────────┐   ┌─────────────────────┐
│ ▣ Spółdz.│ [banner, if any]           │   │ ▣  │                     │   │ ☰  Spółdzielnia AI  │
│ [+ Nowa] │                            │   │ +  │                     │   ├─────────────────────┤
│ MOJA PRACA                            │   │ 📓 │     workspace       │   │                     │
│  Notatnik│        workspace           │   │ 📖 │                     │   │     workspace       │
│  Szkice  │     (scrolls on its own)   │   │ 🖼 │                     │   │  (whole page        │
│ WIEDZA   │                            │   │ 💬 │                     │   │   scrolls)          │
│  Wiki …  │                            │   │ …  │                     │   │                     │
│ ──────── │                            │   │    │                     │   │                     │
│ Twoje dane                            │   │ ⓘ  │                     │   │                     │
│ 👤 Jan  ⏻│                            │   │ 👤 │                     │   └─────────────────────┘
└──────────┴────────────────────────────┘   └────┴─────────────────────┘   ☰ → menu drawer
  16rem, or a 4rem rail (remembered)          rail; expands as overlay
```

### Menu content (grouped like Open Notebook's Collect / Process / Create / Manage)

| Section | Items | Role |
|---|---|---|
| — | **+ Nowa notatka** (primary button; in the rail, a „+” with a tooltip) | member |
| Moja praca | Notatnik, Moje szkice, Moje kontrybucje | member |
| Wiedza spółdzielni | Wiki, Atlas, Zapytaj spółdzielnię | member |
| Rada Ekspertów | Recenzje (tekst, atlas, etykiety) | EXPERT |
| Administracja | Bezpieczeństwo, Modele AI, Użytkownicy, Atlas (admin) | ADMIN |
| Footer | Twoje dane; account name and „Wyloguj”; collapse button | member |

The active item is the **longest matching href prefix** (so `/admin/atlas` does not also light
up `/atlas`). Links stay hidden for roles the account does not have, but the API still enforces
roles regardless. A signed-out visitor sees only the logo and „Zaloguj się”.

## Acceptance criteria

```gherkin
Background: member1 is logged in

Scenario: desktop layout
  Given a 1440×900 window
  Then the menu is on the left and the workspace takes at least 2/3 of the width
  And only the workspace scrolls; the menu stays in place
  And the current page is marked in the menu (aria-current="page")

Scenario: collapsing the menu
  When member1 clicks "Zwiń menu"
  Then the menu becomes an icon rail with a tooltip and an accessible name on each icon
  And after a reload it is still collapsed, with no layout jump on first paint

Scenario: role-based menu
  Then member1 sees no "Rada Ekspertów" or "Administracja" sections
  And expert1 sees "Recenzje", and admin1 sees the "Administracja" section

Scenario: phone navigation
  Given a 390×844 window (iPhone 14)
  Then there is no sidebar, only a top bar with "Otwórz menu"
  When member1 opens the menu and picks "Wiki"
  Then the drawer closes and the wiki is shown
  And Escape or a tap on the backdrop also closes the drawer, and focus returns to "Otwórz menu"

Scenario: notebook on a phone
  Given a 390×844 window and an open note
  Then the tabs "Źródła", "Notatka", "Zapytaj" are shown, with "Notatka" selected
  And typing in the editor, uploading a file and asking a question all work

Scenario: no horizontal scrolling
  Given any page at 360px width
  Then the page does not scroll sideways (tables and code scroll inside their own boxes)
  And every control can be tapped (a target of at least 44×44 CSS px on touch screens)
```

## Constraints (must not regress)

- **CSP stays unchanged** (`style-src 'self' 'nonce-…'`, Trusted Types): no `style=` attributes
  and no runtime-injected styles. Widths and animations use Tailwind classes only. Icons come from
  `lucide-react` (bundled React SVG, no external origin, no `innerHTML`).
- **The menu state is not sensitive.** The collapse preference lives in a `sb=1|0` cookie
  (`SameSite=Lax`, `Path=/`, one year, no `HttpOnly` so the client can write it). The root
  layout already renders per request, so it reads the cookie and renders the right width with
  no flash. Nothing else is stored in the browser.
- **`ProcessorsBanner`** (P4) moves to the top of the workspace and is still shown on every page.
- **Logging out** still clears the query cache (`client.clear()`) before navigating.
- **Accessibility (WCAG 2.2 AA):** landmarks (`nav[aria-label=Główna]`, `main#content`), a
  „Przejdź do treści” skip link, a drawer and dialogs built on native `<dialog>` (focus trap,
  Escape), visible focus, `prefers-reduced-motion` turns off transitions, and contrast is kept in
  both colour schemes.
- **Viewport:** `100dvh` with safe-area insets (`env(safe-area-inset-*)` through Tailwind
  classes), so the iOS address bar and the keyboard do not cover the chat composer or the top bar.
- Existing E2E selectors (`getByRole("link", { name })`, button names) keep working. On a phone
  a link is inside the drawer, so the mobile specs open the drawer first.

## Chunks

### 12.1 App shell: sidebar, rail, phone drawer
- `components/shell/AppShell.tsx` (`flex h-dvh`; `main#content` scrolls by itself),
  `Sidebar.tsx` (sections, icons, „+ Nowa notatka”, account footer, longest-prefix active link),
  `MobileTopBar.tsx` and `MobileDrawer.tsx` (native `<dialog>`, closes on route change),
  `nav-items.ts` (one source of truth for desktop and phone, with roles).
- `lib/sidebar.ts`: reading and writing the `sb` cookie; `layout.tsx` reads it through `cookies()`.
- Tablet (md): rail by default; „Rozwiń” expands the menu as an overlay over the workspace.
- `MainNav.tsx` is removed. The banner moves into the workspace. Skip link.
- Tests (Vitest): role filtering, active-link rule, collapse and cookie, drawer open, close on
  navigate and Escape, logout clears the cache.

### 12.2 Design building blocks
- New dependency: `lucide-react`. Sidebar tokens in `globals.css` (`--sidebar`,
  `--sidebar-accent`, light and dark).
- `ui/PageHeader` (title, description, actions; wraps on a phone), `ui/Tabs` (WAI-ARIA tabs,
  arrow keys; scrollable on a phone), `ui/DataTable` (a table on md+, a card list below),
  `ui/EmptyState`, `ui/Skeleton`.
- `ui/Dialog` becomes a bottom sheet on a phone (full width, `max-h-[90dvh]`, scrolls inside).
- `ui/Button`: a 44px target on coarse pointers (`pointer-coarse:` variant), an `icon` size
  with a required `aria-label`.
- Tests (Vitest) for each building block: keyboard, ARIA, and the phone and desktop variants.

### 12.3 Notebook: list and the Źródła | Notatka | Zapytaj workspace
- `/notebook`: `PageHeader` with „Nowa notatka”, notes as a `DataTable` or cards, `EmptyState`.
- `/notebook/[id]`: xl, three columns `[18rem | 1fr | 22rem]`. lg, two columns with
  „Zapytaj” in a collapsible right panel. Below lg, `Tabs` (Źródła: upload, links, photos,
  derivatives; Notatka: title, editor, autosave state; Zapytaj: `AskPanel`). Each column scrolls
  by itself. Autosave is unaffected by switching tabs: panels are hidden, not unmounted.
- `/notebook/[id]/photos/[attachmentId]`: the annotator uses the whole workspace, and on a phone
  the label list goes below the image.
- Tests: Vitest for the columns and tabs, and the existing `notebook.test.tsx` and
  `photos.test.tsx` stay green.

### 12.4 Wiki, drafts and chat
- Wiki list and article: a readable column (`max-w-3xl`), with block authorship shown as a side
  margin on lg and inline below lg. Drafts use `DataTable`.
- Chat: a full-height conversation, a composer that sticks to the bottom (with safe-area and
  keyboard space), and citations as an expandable list on a phone.

### 12.5 Atlas, contributions, privacy, home
- Atlas gallery: a responsive grid (2 columns on a phone, up to 5 on desktop). The atlas photo
  page puts the image above the details on a phone.
- `/me/contributions` and `/me/drafts`: `DataTable`. Revoke and suspend dialogs as bottom sheets.
- `/privacy`, `/`, error and not-found pages: `PageHeader` and cards in the new style. The home
  page becomes a dashboard of the menu's sections.

### 12.6 Expert review
- `/review`, `/review/atlas`, `/review/labels`: a queue and detail view (side by side on lg,
  with the queue first and then the detail and „← Kolejka” on a phone). `TextDiff` is side by
  side on lg and unified below.

### 12.7 Admin consoles
- `/admin/llm` (Catalog, Routing, Playground, History, IndexPanel): `Tabs` (scrollable on a
  phone), provider and model lists as `DataTable`, forms in one column on a phone.
- `/admin/users`: a list and user panel (master and detail, stacked on a phone).
- `/admin/security`: charts resize to their container, and `Tables` uses `DataTable`.
- `/admin/atlas`: domains and export as `DataTable` and forms.

### 12.8 E2E on phones, accessibility, security review, documentation
- Playwright projects: `chromium` (desktop, as today), `mobile` (Pixel 7) and `tablet` (iPad
  Mini viewport). Scenario 14 `14-app-shell.spec.ts` covers the acceptance criteria above. A
  sample of earlier scenarios (1 auth, 4 happy path, 11 atlas) also runs in `mobile`.
- A check that no page scrolls sideways at 360px, across every route, for member1, expert1 and
  admin1.
- `@axe-core/playwright` on the main routes for both projects: no serious or critical findings.
- Security review entry: CSP unchanged, the `sb` cookie holds no data, the menu shows links only
  (the API enforces roles), the drawer does not survive logout.
- README screenshots, this document, and the US-06 *Delivery* list.

## Verification per chunk

`npm run lint && npm run typecheck && npm test` in `frontend/`, plus `npm run build` (CSP and
Trusted Types are only enforced in a production build), plus the relevant E2E specs on the dev
stack. Backend checks (`ruff`, `pyright`, `pytest`) run as usual. No backend changes are expected.
